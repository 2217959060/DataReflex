# -*- coding: utf-8 -*-
"""数据导入：CSV/Excel 解析、限额、列重命名、公式转义、关联提示。

上游：``/api/datasets/import``。
本文件：只读单表；保留中文列名；坏文件跳过不中断；超限拒绝；样本脱敏。
下游：落盘 dataset + TableSchema，供意图/沙箱使用。

See ``tasks.md`` T10 / D-IMPORT-01。
"""

from __future__ import annotations

import csv
import re
import uuid
from pathlib import Path

import pandas as pd

from data_reflex.config.settings import Settings, get_settings
from data_reflex.models.schemas import (
    ColumnSchema,
    DataImportReq,
    DataImportResp,
    JoinHint,
    TableSchema,
)
from data_reflex.utils.logger import get_logger

logger = get_logger(__name__)

_SUPPORTED_SUFFIXES = {".csv", ".xlsx", ".xlsm", ".xls"}
_FORMULA_PREFIXES = ("=", "+", "@", "\t", "\r")
_SAMPLE_LIMIT = 5

# Fixed join pairs from schema.md §5 (matched by column name across basenames).
_SCHEMA_JOIN_SPECS: tuple[tuple[str, str, str, str, float], ...] = (
    ("orders", "user_id", "users", "user_id", 1.0),
    ("returns", "order_id", "orders", "order_id", 1.0),
    ("returns", "product_id", "orders", "product_id", 0.9),
)

_SENSITIVE_COLUMN_NAMES: frozenset[str] = frozenset(
    {
        "phone",
        "mobile",
        "email",
        "id_card",
        "address",
        "手机",
        "手机号",
        "电话",
        "邮箱",
        "身份证",
        "身份证号",
        "地址",
    }
)


def _stem_key(name: str) -> str:
    """Lowercase stem without extension for join matching."""
    return Path(name).stem.lower()


def escape_formula_value(value: object) -> object:
    """Neutralize CSV/Excel formula-injection prefixes; keep Chinese text intact."""
    if not isinstance(value, str):
        return value
    text = value
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    # Leading '-' only when it looks like a formula, not a plain negative number.
    if text.startswith("-") and not re.match(r"^-?\d+(\.\d+)?$", text):
        return "'" + text
    return text


def dedupe_column_names(columns: list[str]) -> list[str]:
    """Rename duplicates to ``name``, ``name_2``, ``name_3``, …; keep Chinese."""
    seen: dict[str, int] = {}
    result: list[str] = []
    for col in columns:
        base = str(col)
        count = seen.get(base, 0) + 1
        seen[base] = count
        result.append(base if count == 1 else f"{base}_{count}")
    return result


def _normalize_dtype(series: pd.Series) -> str:
    dtype = series.dtype
    if pd.api.types.is_bool_dtype(dtype):
        return "bool"
    if pd.api.types.is_integer_dtype(dtype):
        return "int"
    if pd.api.types.is_float_dtype(dtype):
        return "float"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "datetime"
    # Heuristic dates stored as object/string.
    if pd.api.types.is_object_dtype(dtype) or pd.api.types.is_string_dtype(dtype):
        sample = series.dropna().astype(str).head(20)
        if len(sample) > 0 and sample.str.match(r"^\d{4}-\d{2}-\d{2}$").all():
            return "date"
        return "string"
    return "string"


def _sample_values(series: pd.Series, column_name: str) -> list[str]:
    if column_name in _SENSITIVE_COLUMN_NAMES:
        non_null = int(series.notna().sum())
        return [f"[REDACTED]:n={non_null}"] if non_null else []
    values: list[str] = []
    for raw in series.dropna().tolist():
        escaped = escape_formula_value(raw)
        text = str(escaped)
        if text not in values:
            values.append(text)
        if len(values) >= _SAMPLE_LIMIT:
            break
    return values


def _apply_formula_escape(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_object_dtype(out[col]) or pd.api.types.is_string_dtype(
            out[col]
        ):
            out[col] = out[col].map(
                lambda v: escape_formula_value(v) if pd.notna(v) else v
            )
    return out


def _detect_encoding(path: Path, preferred: str | None) -> str:
    if preferred:
        return preferred
    raw = path.read_bytes()[:4096]
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "utf-8"


def _build_columns(df: pd.DataFrame) -> list[ColumnSchema]:
    columns: list[ColumnSchema] = []
    n = len(df)
    for name in df.columns:
        series = df[name]
        null_ratio = float(series.isna().mean()) if n > 0 else 1.0
        columns.append(
            ColumnSchema(
                name=str(name),
                dtype=_normalize_dtype(series),
                null_ratio=round(null_ratio, 6),
                sample_values=_sample_values(series, str(name)),
            )
        )
    return columns


def _infer_join_hints(tables: list[TableSchema]) -> list[JoinHint]:
    """Attach schema.md joins when both sides exist; else same-name heuristic."""
    by_stem: dict[str, TableSchema] = {_stem_key(t.file_name): t for t in tables}
    hints: list[JoinHint] = []
    seen: set[tuple[str, str, str, str]] = set()

    for left_stem, left_col, right_stem, right_col, confidence in _SCHEMA_JOIN_SPECS:
        left = by_stem.get(left_stem)
        right = by_stem.get(right_stem)
        if left is None or right is None:
            continue
        left_cols = {c.name for c in left.columns}
        right_cols = {c.name for c in right.columns}
        if left_col in left_cols and right_col in right_cols:
            key = (left.file_name, left_col, right.file_name, right_col)
            if key not in seen:
                seen.add(key)
                hints.append(
                    JoinHint(
                        left_file=left.file_name,
                        left_column=left_col,
                        right_file=right.file_name,
                        right_column=right_col,
                        confidence=confidence,
                    )
                )

    # Same column name across two files → weaker hint (skip if already covered).
    for i, left in enumerate(tables):
        left_cols = {c.name for c in left.columns}
        for right in tables[i + 1 :]:
            right_cols = {c.name for c in right.columns}
            for col in sorted(left_cols & right_cols):
                key = (left.file_name, col, right.file_name, col)
                rev = (right.file_name, col, left.file_name, col)
                if key in seen or rev in seen:
                    continue
                seen.add(key)
                hints.append(
                    JoinHint(
                        left_file=left.file_name,
                        left_column=col,
                        right_file=right.file_name,
                        right_column=col,
                        confidence=0.7,
                    )
                )
    return hints


class DataImporter:
    """导入文件并产出结构摘要（schema / join hints / 告警）。"""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        dataset_root: str | Path | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        root = dataset_root
        if root is None:
            # Place datasets next to the configured sqlite parent: data/datasets
            sqlite_parent = Path(self._settings.sqlite_path).parent
            root = sqlite_parent / "datasets"
        self._dataset_root = Path(root)

    def import_files(self, req: DataImportReq) -> DataImportResp:
        """Parse files; reject on hard limits; skip bad files with warnings."""
        paths = [Path(p) for p in req.file_paths]
        max_files = self._settings.max_upload_files
        max_bytes = self._settings.max_file_size_mb * 1024 * 1024

        if len(paths) > max_files:
            msg = (
                f"upload rejected: file count {len(paths)} exceeds "
                f"max_upload_files={max_files}"
            )
            logger.warning(msg, extra={"task_id": "-", "dataset_id": "-"})
            return DataImportResp(
                dataset_id="",
                success=False,
                tables=[],
                warnings=[],
                error_msg=msg,
            )

        oversized = [p for p in paths if p.is_file() and p.stat().st_size > max_bytes]
        if oversized:
            names = ", ".join(p.name for p in oversized)
            msg = (
                f"upload rejected: file(s) exceed max_file_size_mb="
                f"{self._settings.max_file_size_mb}: {names}"
            )
            logger.warning(msg, extra={"task_id": "-", "dataset_id": "-"})
            return DataImportResp(
                dataset_id="",
                success=False,
                tables=[],
                warnings=[],
                error_msg=msg,
            )

        dataset_id = str(uuid.uuid4())
        dest_dir = self._dataset_root / dataset_id
        dest_dir.mkdir(parents=True, exist_ok=True)

        tables: list[TableSchema] = []
        warnings: list[str] = []

        for path in paths:
            try:
                table, file_warnings = self._import_one(
                    path, dest_dir, encoding=req.file_encoding
                )
                warnings.extend(file_warnings)
                if table is not None:
                    tables.append(table)
            except Exception as exc:  # noqa: BLE001 — per-file isolation
                warnings.append(f"skipped {path.name}: {type(exc).__name__}: {exc}")
                logger.warning(
                    "import skipped file",
                    extra={"task_id": "-", "dataset_id": dataset_id},
                )

        if not tables:
            return DataImportResp(
                dataset_id=dataset_id,
                success=False,
                tables=[],
                warnings=warnings,
                error_msg="no valid tables imported",
            )

        join_hints = _infer_join_hints(tables)
        # Attach all join hints that involve each table onto that table's list.
        for table in tables:
            table.join_hints = [
                h
                for h in join_hints
                if h.left_file == table.file_name or h.right_file == table.file_name
            ]

        logger.info(
            "import completed",
            extra={"task_id": "-", "dataset_id": dataset_id},
        )
        return DataImportResp(
            dataset_id=dataset_id,
            success=True,
            tables=tables,
            warnings=warnings,
            error_msg=None,
        )

    def _import_one(
        self,
        path: Path,
        dest_dir: Path,
        *,
        encoding: str | None,
    ) -> tuple[TableSchema | None, list[str]]:
        warnings: list[str] = []
        if not path.exists():
            warnings.append(f"skipped {path.name}: file not found")
            return None, warnings
        if not path.is_file():
            warnings.append(f"skipped {path.name}: not a file")
            return None, warnings
        if path.stat().st_size == 0:
            warnings.append(f"skipped {path.name}: empty file")
            return None, warnings

        suffix = path.suffix.lower()
        if suffix not in _SUPPORTED_SUFFIXES:
            warnings.append(
                f"skipped {path.name}: unsupported type {suffix or '(none)'}"
            )
            return None, warnings

        # Reject obvious binary payloads early (null bytes), even with .csv suffix.
        head = path.read_bytes()[:8192]
        if suffix == ".csv" and b"\x00" in head:
            warnings.append(f"skipped {path.name}: binary/non-text content")
            return None, warnings

        if suffix == ".csv":
            df, w = self._read_csv(path, encoding=encoding)
            warnings.extend(w)
        else:
            df, w = self._read_excel(path)
            warnings.extend(w)

        if df is None:
            return None, warnings
        if df.shape[1] == 0:
            warnings.append(f"skipped {path.name}: no columns")
            return None, warnings

        df.columns = dedupe_column_names([str(c) for c in df.columns.tolist()])
        df = _apply_formula_escape(df)

        # Persist cleaned copy for later sandbox mount (basename preserved).
        if suffix == ".csv":
            dest = dest_dir / path.name
            df.to_csv(dest, index=False, encoding="utf-8")
        else:
            dest = dest_dir / f"{path.stem}.xlsx"
            df.to_excel(dest, index=False, engine="openpyxl")

        return (
            TableSchema(
                file_name=path.name,
                row_count=int(len(df)),
                columns=_build_columns(df),
                join_hints=[],
            ),
            warnings,
        )

    def _read_csv(
        self, path: Path, *, encoding: str | None
    ) -> tuple[pd.DataFrame | None, list[str]]:
        warnings: list[str] = []
        enc = _detect_encoding(path, encoding)
        try:
            # Read raw header first — pandas would mangle dupes to ``name.1``.
            with path.open(encoding=enc, newline="") as fh:
                raw_header = next(csv.reader(fh))
            df = pd.read_csv(path, encoding=enc, header=0)
            if len(raw_header) == len(df.columns):
                df.columns = dedupe_column_names([str(c) for c in raw_header])
            else:
                df.columns = dedupe_column_names([str(c) for c in df.columns.tolist()])
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"skipped {path.name}: CSV parse failed ({exc})")
            return None, warnings
        return df, warnings

    def _read_excel(self, path: Path) -> tuple[pd.DataFrame | None, list[str]]:
        warnings: list[str] = []
        try:
            # openpyxl never executes macros; we only read cell values.
            from openpyxl import load_workbook

            wb = load_workbook(path, read_only=True, data_only=True, keep_vba=False)
            try:
                if len(wb.sheetnames) > 1:
                    warnings.append(
                        f"{path.name}: multiple sheets detected; only first sheet "
                        f"'{wb.sheetnames[0]}' is imported (v1 single-sheet)"
                    )
                ws = wb[wb.sheetnames[0]]
                rows = ws.iter_rows(values_only=True)
                raw_header_row = next(rows, None)
                if raw_header_row is None:
                    warnings.append(f"skipped {path.name}: empty sheet")
                    return None, warnings
                raw_header = ["" if c is None else str(c) for c in raw_header_row]
                data_rows = [list(r) for r in rows]
            finally:
                wb.close()

            columns = dedupe_column_names(raw_header)
            df = pd.DataFrame(data_rows, columns=columns)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"skipped {path.name}: Excel parse failed ({exc})")
            return None, warnings
        return df, warnings


# Re-export helpers for unit tests.
__all__ = [
    "DataImporter",
    "dedupe_column_names",
    "escape_formula_value",
]
