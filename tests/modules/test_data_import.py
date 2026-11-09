# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from data_reflex.config.settings import Settings
from data_reflex.models.schemas import DataImportReq
from data_reflex.modules.data_import.importer import (
    DataImporter,
    dedupe_column_names,
    escape_formula_value,
)

FIXTURES = Path(__file__).resolve().parents[2] / "eval" / "datasets" / "fixtures"


@pytest.fixture()
def importer(tmp_path: Path) -> DataImporter:
    settings = Settings(
        max_upload_files=5,
        max_file_size_mb=1,
    )
    return DataImporter(settings=settings, dataset_root=tmp_path / "datasets")


def test_dedupe_column_names_keeps_chinese() -> None:
    cols = dedupe_column_names(["渠道", "渠道", "金额", "渠道"])
    assert cols == ["渠道", "渠道_2", "金额", "渠道_3"]


def test_escape_formula_prefix() -> None:
    assert escape_formula_value("=CMD|calc") == "'=CMD|calc"
    assert escape_formula_value("+1+1") == "'+1+1"
    assert escape_formula_value("@SUM(A1)") == "'@SUM(A1)"
    assert escape_formula_value("-1.5") == "-1.5"
    assert escape_formula_value("-SUM(A1)") == "'-SUM(A1)"
    assert escape_formula_value("无线耳机") == "无线耳机"


def test_import_fixtures_with_join_hints(importer: DataImporter) -> None:
    req = DataImportReq(
        file_paths=[
            str(FIXTURES / "orders.csv"),
            str(FIXTURES / "returns.csv"),
            str(FIXTURES / "users.csv"),
        ]
    )
    resp = importer.import_files(req)
    assert resp.success is True
    assert resp.dataset_id
    assert len(resp.tables) == 3
    names = {t.file_name for t in resp.tables}
    assert names == {"orders.csv", "returns.csv", "users.csv"}
    # At least the schema.md high-confidence joins should appear.
    all_hints = [h for t in resp.tables for h in t.join_hints]
    pairs = {(h.left_column, h.right_column, h.confidence) for h in all_hints}
    assert ("user_id", "user_id", 1.0) in pairs
    assert ("order_id", "order_id", 1.0) in pairs


def test_duplicate_and_chinese_columns(importer: DataImporter, tmp_path: Path) -> None:
    csv_path = tmp_path / "中文表.csv"
    csv_path.write_text("渠道,渠道,产品名称\nAPP,官网,耳机\n", encoding="utf-8")
    resp = importer.import_files(DataImportReq(file_paths=[str(csv_path)]))
    assert resp.success is True
    cols = [c.name for c in resp.tables[0].columns]
    assert cols == ["渠道", "渠道_2", "产品名称"]


def test_formula_prefix_escaped_in_samples(
    importer: DataImporter, tmp_path: Path
) -> None:
    csv_path = tmp_path / "formula.csv"
    csv_path.write_text("name,note\nA,=1+1\nB,ok\n", encoding="utf-8")
    resp = importer.import_files(DataImportReq(file_paths=[str(csv_path)]))
    assert resp.success is True
    note_col = next(c for c in resp.tables[0].columns if c.name == "note")
    assert any(v.startswith("'=") for v in note_col.sample_values)
    assert "=1+1" not in note_col.sample_values


def test_reject_too_many_files(importer: DataImporter, tmp_path: Path) -> None:
    paths: list[str] = []
    for i in range(6):
        p = tmp_path / f"f{i}.csv"
        p.write_text("a\n1\n", encoding="utf-8")
        paths.append(str(p))
    resp = importer.import_files(DataImportReq(file_paths=paths))
    assert resp.success is False
    assert resp.error_msg is not None
    assert "max_upload_files" in resp.error_msg
    assert resp.tables == []


def test_reject_oversized_file(tmp_path: Path) -> None:
    settings = Settings(max_upload_files=5, max_file_size_mb=1)
    importer = DataImporter(settings=settings, dataset_root=tmp_path / "datasets")
    big = tmp_path / "big.csv"
    # Slightly over 1 MiB.
    big.write_bytes(b"a\n" + b"x" * (1024 * 1024 + 10))
    resp = importer.import_files(DataImportReq(file_paths=[str(big)]))
    assert resp.success is False
    assert resp.error_msg is not None
    assert "max_file_size_mb" in resp.error_msg


def test_empty_and_binary_do_not_block_others(
    importer: DataImporter, tmp_path: Path
) -> None:
    good = tmp_path / "good.csv"
    good.write_text("id,name\n1,alice\n", encoding="utf-8")
    empty = tmp_path / "empty.csv"
    empty.write_bytes(b"")
    binary = tmp_path / "junk.csv"
    binary.write_bytes(bytes(range(256)) * 4)

    resp = importer.import_files(
        DataImportReq(file_paths=[str(empty), str(binary), str(good)])
    )
    assert resp.success is True
    assert len(resp.tables) == 1
    assert resp.tables[0].file_name == "good.csv"
    assert any("empty" in w for w in resp.warnings)
    assert any("skipped" in w for w in resp.warnings)


def test_excel_single_sheet_only(importer: DataImporter, tmp_path: Path) -> None:
    path = tmp_path / "multi.xlsx"
    wb = Workbook()
    ws1 = wb.active
    assert ws1 is not None
    ws1.title = "first"
    ws1.append(["id", "值"])
    ws1.append([1, "一"])
    ws2 = wb.create_sheet("second")
    ws2.append(["id", "值"])
    ws2.append([2, "二"])
    wb.save(path)

    resp = importer.import_files(DataImportReq(file_paths=[str(path)]))
    assert resp.success is True
    assert len(resp.tables) == 1
    assert resp.tables[0].row_count == 1
    assert any("multiple sheets" in w for w in resp.warnings)
    col_names = [c.name for c in resp.tables[0].columns]
    assert "值" in col_names


def test_sensitive_sample_values_redacted(
    importer: DataImporter, tmp_path: Path
) -> None:
    path = tmp_path / "users_mini.csv"
    path.write_text(
        "user_id,phone,email\nU1,13800000000,a@b.com\n",
        encoding="utf-8",
    )
    resp = importer.import_files(DataImportReq(file_paths=[str(path)]))
    phone = next(c for c in resp.tables[0].columns if c.name == "phone")
    email = next(c for c in resp.tables[0].columns if c.name == "email")
    assert phone.sample_values == ["[REDACTED]:n=1"]
    assert email.sample_values == ["[REDACTED]:n=1"]
    assert "13800000000" not in str(phone.sample_values)
