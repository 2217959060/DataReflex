# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

import pytest

from data_reflex.graph_engine.nodes.error_classify import NodeErrorClassify
from data_reflex.models.schemas import ExecuteResp, GenerateOpResp
from data_reflex.models.state import StrategyFlags, create_initial_task_state
from data_reflex.utils.tracing import create_tracer, reset_tracer


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


def _state(
    *,
    execute_resp: ExecuteResp | None,
    op_type: str = "sql",
    fingerprints: list[str] | None = None,
    sub_id: str = "st-1",
) -> dict[str, Any]:
    state: dict[str, Any] = dict(
        create_initial_task_state(
            dataset_id="ds-1",
            user_query="渠道对比",
            task_id="t-errcls",
            strategy_flags=StrategyFlags(),
        )
    )
    state["current_sub_task_id"] = sub_id
    state["current_op"] = GenerateOpResp(
        op_type=op_type,  # type: ignore[arg-type]
        op_content="SELECT 1",
        format_valid=True,
    )
    state["execute_resp"] = execute_resp
    if fingerprints is not None:
        state["error_fingerprints"] = fingerprints
    return state


def test_syntax_error_classifies_and_routes_reflect() -> None:
    node = NodeErrorClassify()
    resp = ExecuteResp(
        success=False,
        stderr="SQL syntax error near unexpected token 'FROM'",
        exit_code=1,
    )
    delta = node.run(_state(execute_resp=resp))  # type: ignore[arg-type]
    classified = delta["error_classify"]
    assert classified.error_type == "syntax"
    assert classified.retryable is True
    assert classified.suggested_fix
    assert "syntax" in classified.error_fingerprint
    assert classified.error_fingerprint in delta["error_fingerprints"]
    assert delta["next_node"] == "reflect"
    assert delta["sub_task_results"][0].error_summary
    assert delta["sub_task_results"][0].status == "failed"


def test_runtime_error_classifies() -> None:
    node = NodeErrorClassify()
    resp = ExecuteResp(
        success=False,
        stderr="ZeroDivisionError: division by zero",
        exit_code=1,
        resource_killed=False,
    )
    delta = node.run(_state(execute_resp=resp, op_type="python"))  # type: ignore[arg-type]
    classified = delta["error_classify"]
    assert classified.error_type == "runtime"
    assert classified.retryable is True
    assert classified.error_fingerprint in delta["error_fingerprints"]
    assert delta["next_node"] == "reflect"


def test_reflector_off_routes_retryable_straight_to_report() -> None:
    node = NodeErrorClassify()
    resp = ExecuteResp(
        success=False,
        stderr="SQL syntax error near unexpected token 'FROM'",
        exit_code=1,
    )
    state = _state(execute_resp=resp)
    state["strategy_flags"] = StrategyFlags(enable_reflector=False)
    delta = node.run(state)  # type: ignore[arg-type]
    assert delta["error_classify"].retryable is True
    assert delta["next_node"] == "report"


def test_resource_killed_not_retryable_routes_report() -> None:
    node = NodeErrorClassify()
    resp = ExecuteResp(
        success=False,
        stderr="execution timed out after 60s",
        exit_code=124,
        resource_killed=True,
    )
    delta = node.run(_state(execute_resp=resp))  # type: ignore[arg-type]
    classified = delta["error_classify"]
    assert classified.error_type == "runtime"
    assert classified.retryable is False
    assert classified.error_fingerprint in delta["error_fingerprints"]
    assert delta["next_node"] == "report"
    assert delta["sub_task_results"][0].status == "failed"


def test_fingerprint_appended_to_existing_history() -> None:
    node = NodeErrorClassify()
    resp = ExecuteResp(
        success=False,
        stderr="SQL syntax error near unexpected token 'GROUP'",
        exit_code=1,
    )
    delta = node.run(
        _state(execute_resp=resp, fingerprints=["runtime|prior"])  # type: ignore[arg-type]
    )
    assert delta["error_fingerprints"][0] == "runtime|prior"
    assert len(delta["error_fingerprints"]) == 2
    assert delta["error_fingerprints"][1].startswith("syntax|")


def test_missing_execute_resp_synthesizes_from_op_error() -> None:
    node = NodeErrorClassify()
    state = _state(execute_resp=None)
    state["current_op"] = GenerateOpResp(
        op_type="sql",
        op_content="",
        format_valid=False,
        error_msg="SQL syntax error: empty statement",
    )
    delta = node.run(state)  # type: ignore[arg-type]
    assert delta["execute_resp"].success is False
    assert delta["error_classify"].error_type in {"syntax", "runtime"}
    assert delta["error_fingerprints"]
