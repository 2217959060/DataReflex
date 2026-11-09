# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_reflex.models import (
    DataImportReq,
    ExecuteReq,
    FallbackReportResp,
    ReflectReq,
    SubTask,
)


def _minimal_reflect_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "sub_task": SubTask(task_id="st1", description="demo"),
        "error_classify": {
            "error_type": "syntax",
            "error_detail": "bad token",
            "error_fingerprint": "syntax:bad",
            "retryable": True,
        },
        "retry_count": 0,
        "max_retry": 3,
        "llm_calls_used": 1,
        "max_llm_calls": 20,
        "previous_fingerprints": [],
    }
    base.update(overrides)
    return base


def test_illegal_field_rejected_by_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        DataImportReq(file_paths=["a.csv"], unknown_field=True)  # type: ignore[call-arg]


def test_max_retry_negative_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        ReflectReq(**_minimal_reflect_kwargs(max_retry=-1))  # type: ignore[arg-type]


def test_execute_req_requires_positive_limits() -> None:
    with pytest.raises(ValidationError):
        ExecuteReq(
            op_type="sql",
            op_content="SELECT 1",
            dataset_id="ds1",
            timeout_seconds=0,
            memory_limit_mb=512,
            cpu_limit=1.0,
        )


def test_fallback_report_cannot_claim_analysis_conclusion() -> None:
    with pytest.raises(ValidationError):
        FallbackReportResp(
            summary="x",
            is_analysis_conclusion=True,
        )


def test_valid_reflect_req_accepts_zero_retry() -> None:
    req = ReflectReq(**_minimal_reflect_kwargs(max_retry=0))  # type: ignore[arg-type]
    assert req.max_retry == 0
