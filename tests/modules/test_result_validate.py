# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from data_reflex.models.schemas import ExecuteResp, ResultValidateReq
from data_reflex.modules.result_validate.validator import ResultValidator


@pytest.fixture()
def validator() -> ResultValidator:
    return ResultValidator()


def _ok_resp(**kwargs: object) -> ExecuteResp:
    base: dict[str, object] = {
        "success": True,
        "result_rows": [{"product": "A", "rate": 0.2, "amount": 10.0}],
        "result_columns": ["product", "rate", "amount"],
    }
    base.update(kwargs)
    return ExecuteResp.model_validate(base)


def test_valid_result_passes(validator: ResultValidator) -> None:
    req = ResultValidateReq(
        execute_resp=_ok_resp(),
        structured_constraints=[
            {"type": "require_columns", "columns": ["product", "rate"]},
            {"type": "ratio_range", "column": "rate", "min": 0.0, "max": 1.0},
            {"type": "max_rows", "max": 100},
        ],
    )
    out = validator.validate(req)
    assert out.valid is True
    assert out.anomaly_type is None


def test_ratio_out_of_range(validator: ResultValidator) -> None:
    req = ResultValidateReq(
        execute_resp=_ok_resp(result_rows=[{"rate": 1.5}]),
        structured_constraints=[
            {"type": "ratio_range", "column": "rate", "min": 0.0, "max": 1.0}
        ],
    )
    out = validator.validate(req)
    assert out.valid is False
    assert out.anomaly_type == "ratio_out_of_range"
    assert out.anomaly_detail


def test_empty_anomaly(validator: ResultValidator) -> None:
    req = ResultValidateReq(
        execute_resp=_ok_resp(result_rows=[], result_columns=[]),
        structured_constraints=[{"type": "non_empty"}],
    )
    out = validator.validate(req)
    assert out.valid is False
    assert out.anomaly_type == "empty_anomaly"


def test_legal_empty_not_flagged(validator: ResultValidator) -> None:
    req = ResultValidateReq(
        execute_resp=_ok_resp(result_rows=[], result_columns=["product"]),
        structured_constraints=[{"type": "allow_empty"}],
    )
    out = validator.validate(req)
    assert out.valid is True
    assert out.anomaly_type is None


def test_legal_empty_default_without_non_empty(validator: ResultValidator) -> None:
    """No non_empty constraint → empty is legal (合法空)."""
    req = ResultValidateReq(
        execute_resp=_ok_resp(result_rows=[], result_columns=[]),
        structured_constraints=[],
    )
    out = validator.validate(req)
    assert out.valid is True


def test_type_anomaly(validator: ResultValidator) -> None:
    req = ResultValidateReq(
        execute_resp=_ok_resp(
            result_rows=[{"amount": "not-a-number"}],
            result_columns=["amount"],
        ),
        structured_constraints=[
            {"type": "column_types", "types": {"amount": "number"}}
        ],
    )
    out = validator.validate(req)
    assert out.valid is False
    assert out.anomaly_type == "type_anomaly"


def test_constraint_mismatch_missing_column(validator: ResultValidator) -> None:
    req = ResultValidateReq(
        execute_resp=_ok_resp(
            result_rows=[{"product": "A"}],
            result_columns=["product"],
        ),
        structured_constraints=[
            {"type": "require_columns", "columns": ["product", "rate"]}
        ],
    )
    out = validator.validate(req)
    assert out.valid is False
    assert out.anomaly_type == "constraint_mismatch"
    assert "rate" in (out.anomaly_detail or "")


def test_constraint_mismatch_max_rows(validator: ResultValidator) -> None:
    rows = [{"id": i} for i in range(5)]
    req = ResultValidateReq(
        execute_resp=_ok_resp(result_rows=rows, result_columns=["id"]),
        structured_constraints=[{"type": "max_rows", "max": 3}],
    )
    out = validator.validate(req)
    assert out.valid is False
    assert out.anomaly_type == "constraint_mismatch"
    assert "max_rows" in (out.anomaly_detail or "")


def test_natural_language_constraint_ignored(validator: ResultValidator) -> None:
    """NL constraints must not fake-validate or fail a good result."""
    req = ResultValidateReq(
        execute_resp=_ok_resp(),
        structured_constraints=[
            {"type": "nl_constraint", "text": "排除促销订单"},
            {"type": "require_columns", "columns": ["product"]},
        ],
    )
    out = validator.validate(req)
    assert out.valid is True


def test_failed_execute_is_mismatch(validator: ResultValidator) -> None:
    req = ResultValidateReq(
        execute_resp=ExecuteResp(success=False, stderr="boom", exit_code=1),
        structured_constraints=[{"type": "non_empty"}],
    )
    out = validator.validate(req)
    assert out.valid is False
    assert out.anomaly_type == "constraint_mismatch"
