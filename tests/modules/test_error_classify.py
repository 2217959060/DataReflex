# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from data_reflex.models.schemas import ErrorClassifyReq, ExecuteResp
from data_reflex.modules.error_classify.classifier import (
    ErrorClassifier,
    normalize_error_line,
)


@pytest.fixture()
def classifier() -> ErrorClassifier:
    return ErrorClassifier()


def _req(resp: ExecuteResp, op_type: str = "sql") -> ErrorClassifyReq:
    assert op_type in ("sql", "python")
    return ErrorClassifyReq(execute_resp=resp, op_type=op_type)  # type: ignore[arg-type]


def test_success_is_none(classifier: ErrorClassifier) -> None:
    resp = ExecuteResp(success=True, result_rows=[{"a": 1}], result_columns=["a"])
    out = classifier.classify(_req(resp))
    assert out.error_type == "none"
    assert out.retryable is False
    assert out.error_fingerprint.startswith("none|")
    assert out.suggested_fix is None


def test_syntax_error(classifier: ErrorClassifier) -> None:
    resp = ExecuteResp(
        success=False,
        stderr="SQL syntax error near unexpected token 'FROM'",
        exit_code=1,
    )
    out = classifier.classify(_req(resp))
    assert out.error_type == "syntax"
    assert out.retryable is True
    assert out.suggested_fix
    assert "syntax" in out.error_fingerprint


def test_schema_semantic_error(classifier: ErrorClassifier) -> None:
    resp = ExecuteResp(
        success=False,
        stderr="Error: no such column: phone_number",
        exit_code=1,
    )
    out = classifier.classify(_req(resp))
    assert out.error_type == "schema_semantic"
    assert out.retryable is True
    assert "schema_semantic" in out.error_fingerprint


def test_runtime_timeout_not_retryable(classifier: ErrorClassifier) -> None:
    resp = ExecuteResp(
        success=False,
        stderr="execution timed out after 15s",
        exit_code=124,
        resource_killed=True,
    )
    out = classifier.classify(_req(resp))
    assert out.error_type == "runtime"
    assert out.retryable is False
    assert out.suggested_fix


def test_runtime_generic_retryable(classifier: ErrorClassifier) -> None:
    resp = ExecuteResp(
        success=False,
        stderr="ZeroDivisionError: division by zero",
        exit_code=1,
        resource_killed=False,
    )
    out = classifier.classify(_req(resp, op_type="python"))
    assert out.error_type == "runtime"
    assert out.retryable is True


def test_result_anomaly(classifier: ErrorClassifier) -> None:
    resp = ExecuteResp(
        success=False,
        stderr="anomalous empty result for required aggregation",
        exit_code=1,
    )
    out = classifier.classify(_req(resp))
    assert out.error_type == "result_anomaly"
    assert out.retryable is True
    assert "result_anomaly" in out.error_fingerprint


def test_fingerprint_stable_for_same_error(classifier: ErrorClassifier) -> None:
    resp = ExecuteResp(
        success=False,
        stderr="SyntaxError: invalid syntax at line 3",
        exit_code=1,
    )
    a = classifier.classify(_req(resp, op_type="python"))
    b = classifier.classify(_req(resp, op_type="python"))
    assert a.error_fingerprint == b.error_fingerprint
    assert a.error_type == "syntax"


def test_fingerprint_differs_across_error_kinds(classifier: ErrorClassifier) -> None:
    syntax = classifier.classify(
        _req(
            ExecuteResp(
                success=False, stderr="SyntaxError: invalid syntax", exit_code=1
            ),
            op_type="python",
        )
    )
    schema = classifier.classify(
        _req(
            ExecuteResp(
                success=False, stderr="no such column: missing_col", exit_code=1
            )
        )
    )
    assert syntax.error_fingerprint != schema.error_fingerprint


def test_fingerprint_normalizes_volatile_tokens(classifier: ErrorClassifier) -> None:
    a = ExecuteResp(
        success=False,
        stderr=r"Error at C:\Users\tmp\run_12345\op.py line 12 code 0xdead",
        exit_code=1,
    )
    b = ExecuteResp(
        success=False,
        stderr=r"Error at D:\Other\run_99999\op.py line 12 code 0xbeef",
        exit_code=1,
    )
    # Force runtime (no syntax/schema keywords) so fingerprint compares normalized line.
    fa = classifier.classify(_req(a, op_type="python")).error_fingerprint
    fb = classifier.classify(_req(b, op_type="python")).error_fingerprint
    assert fa == fb
    assert "<path>" in normalize_error_line(a.stderr)
    assert "<n>" in normalize_error_line(a.stderr) or "<hex>" in normalize_error_line(
        a.stderr
    )


def test_python_syntax_error_classified(classifier: ErrorClassifier) -> None:
    resp = ExecuteResp(
        success=False,
        stderr="File 'op.py', line 1\n    def broken(\nSyntaxError: unexpected EOF",
        exit_code=1,
    )
    out = classifier.classify(_req(resp, op_type="python"))
    assert out.error_type == "syntax"
