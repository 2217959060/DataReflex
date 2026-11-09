# -*- coding: utf-8 -*-
"""结果校验：只认结构化约束；未结构化的自然语言约束直接忽略（不假装检过）。

上游：``Node_ResultValidate``（execute 成功之后）。
本文件：空表/比例/类型/列约束等 → ``anomaly_type``。
下游：节点决定回 generate_op 自愈或去 report。

See ``tasks.md`` T15 / D-RESULT-01。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from data_reflex.models.schemas import ResultValidateReq, ResultValidateResp
from data_reflex.utils.logger import get_logger

logger = get_logger(__name__)

AnomalyType = Literal[
    "empty_anomaly",
    "ratio_out_of_range",
    "type_anomaly",
    "constraint_mismatch",
]

# Constraint ``type`` values we understand. Anything else (incl. nl_*) is skipped.
_KNOWN_TYPES = frozenset(
    {
        "require_columns",
        "non_empty",
        "allow_empty",
        "max_rows",
        "ratio_range",
        "column_types",
    }
)


def _fail(anomaly_type: AnomalyType, detail: str) -> ResultValidateResp:
    return ResultValidateResp(
        valid=False, anomaly_type=anomaly_type, anomaly_detail=detail
    )


def _ok() -> ResultValidateResp:
    return ResultValidateResp(valid=True, anomaly_type=None, anomaly_detail=None)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_string(value: object) -> bool:
    return isinstance(value, str)


class ResultValidator:
    """按已知 constraint type 检查结果；未知/nl_* 类型跳过不报假阳性。"""

    def validate(self, req: ResultValidateReq) -> ResultValidateResp:
        resp = req.execute_resp
        constraints: list[Mapping[str, Any]] = [
            c
            for c in req.structured_constraints
            if isinstance(c, Mapping) and str(c.get("type", "")) in _KNOWN_TYPES
        ]
        ignored = [
            c
            for c in req.structured_constraints
            if not (isinstance(c, Mapping) and str(c.get("type", "")) in _KNOWN_TYPES)
        ]
        if ignored:
            logger.info(
                "skipped non-structured constraints",
                extra={
                    "task_id": "-",
                    "dataset_id": "-",
                    "skipped_count": len(ignored),
                },
            )

        if not resp.success:
            return _fail(
                "constraint_mismatch",
                "execute_resp.success is False; result validation requires success",
            )

        rows = resp.result_rows
        columns = list(resp.result_columns) or (list(rows[0].keys()) if rows else [])

        require_non_empty = any(c.get("type") == "non_empty" for c in constraints)
        allow_empty = any(c.get("type") == "allow_empty" for c in constraints)
        # Default: empty is legal unless non_empty is explicitly required.
        empty_is_anomaly = require_non_empty and not allow_empty

        if not rows:
            if empty_is_anomaly:
                return _fail(
                    "empty_anomaly",
                    "result is empty but constraint non_empty requires rows",
                )
            # Legal empty — still check column requirements if declared on empty set.
            col_fail = self._check_require_columns(constraints, columns, rows)
            if col_fail is not None:
                return col_fail
            return _ok()

        # Order: columns → max_rows → types → ratios (stable, testable).
        col_fail = self._check_require_columns(constraints, columns, rows)
        if col_fail is not None:
            return col_fail

        max_fail = self._check_max_rows(constraints, len(rows))
        if max_fail is not None:
            return max_fail

        type_fail = self._check_column_types(constraints, rows)
        if type_fail is not None:
            return type_fail

        ratio_fail = self._check_ratio_ranges(constraints, rows)
        if ratio_fail is not None:
            return ratio_fail

        logger.info(
            "result validation passed",
            extra={"task_id": "-", "dataset_id": "-", "row_count": len(rows)},
        )
        return _ok()

    def _check_require_columns(
        self,
        constraints: Sequence[Mapping[str, Any]],
        columns: list[str],
        rows: list[dict[str, Any]],
    ) -> ResultValidateResp | None:
        present = {c.lower() for c in columns}
        if rows:
            for row in rows:
                present |= {str(k).lower() for k in row}
        for cons in constraints:
            if cons.get("type") != "require_columns":
                continue
            needed = [str(x) for x in cons.get("columns", [])]
            missing = [c for c in needed if c.lower() not in present]
            if missing:
                return _fail(
                    "constraint_mismatch",
                    "missing required columns: " + ", ".join(missing),
                )
        return None

    def _check_max_rows(
        self, constraints: Sequence[Mapping[str, Any]], row_count: int
    ) -> ResultValidateResp | None:
        for cons in constraints:
            if cons.get("type") != "max_rows":
                continue
            try:
                limit = int(cons["max"])
            except (KeyError, TypeError, ValueError):
                return _fail(
                    "constraint_mismatch",
                    "max_rows constraint missing valid 'max' integer",
                )
            if row_count > limit:
                return _fail(
                    "constraint_mismatch",
                    f"row_count {row_count} exceeds max_rows {limit}",
                )
        return None

    def _check_column_types(
        self, constraints: Sequence[Mapping[str, Any]], rows: list[dict[str, Any]]
    ) -> ResultValidateResp | None:
        for cons in constraints:
            if cons.get("type") != "column_types":
                continue
            types = cons.get("types")
            if not isinstance(types, Mapping):
                return _fail(
                    "constraint_mismatch",
                    "column_types constraint requires mapping 'types'",
                )
            for col, expected in types.items():
                col_s = str(col)
                exp = str(expected).lower()
                for idx, row in enumerate(rows):
                    if col_s not in row:
                        # Missing column vs type — treat as mismatch.
                        return _fail(
                            "constraint_mismatch",
                            f"column {col_s!r} missing while typed as {exp}",
                        )
                    value = row[col_s]
                    if value is None:
                        continue
                    ok = False
                    if exp in {"number", "float", "int", "numeric"}:
                        ok = _is_number(value)
                        if exp == "int":
                            ok = isinstance(value, int) and not isinstance(value, bool)
                    elif exp in {"string", "str", "text"}:
                        ok = _is_string(value)
                    elif exp in {"bool", "boolean"}:
                        ok = isinstance(value, bool)
                    else:
                        return _fail(
                            "constraint_mismatch",
                            f"unsupported type name in column_types: {exp}",
                        )
                    if not ok:
                        return _fail(
                            "type_anomaly",
                            f"column {col_s!r} row {idx} expected {exp}, "
                            f"got {type(value).__name__}",
                        )
        return None

    def _check_ratio_ranges(
        self, constraints: Sequence[Mapping[str, Any]], rows: list[dict[str, Any]]
    ) -> ResultValidateResp | None:
        for cons in constraints:
            if cons.get("type") != "ratio_range":
                continue
            col = str(cons.get("column", ""))
            if not col:
                return _fail(
                    "constraint_mismatch",
                    "ratio_range constraint requires 'column'",
                )
            try:
                lo = float(cons.get("min", 0.0))
                hi = float(cons.get("max", 1.0))
            except (TypeError, ValueError):
                return _fail(
                    "constraint_mismatch",
                    "ratio_range min/max must be numeric",
                )
            for idx, row in enumerate(rows):
                if col not in row:
                    return _fail(
                        "constraint_mismatch",
                        f"ratio column {col!r} missing in row {idx}",
                    )
                value = row[col]
                if not _is_number(value):
                    return _fail(
                        "type_anomaly",
                        f"ratio column {col!r} row {idx} is not numeric",
                    )
                num = float(value)
                if num < lo or num > hi:
                    return _fail(
                        "ratio_out_of_range",
                        f"column {col!r} value {num} outside [{lo}, {hi}] "
                        f"at row {idx}",
                    )
        return None
