# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

import pytest

from data_reflex.exceptions import SandboxUnavailableError
from data_reflex.graph_engine.nodes.execute import NodeExecute
from data_reflex.models.schemas import ExecuteReq, ExecuteResp, GenerateOpResp
from data_reflex.models.state import StrategyFlags, create_initial_task_state
from data_reflex.modules.executor.mock_executor import MockSandboxExecutor, MockScenario
from data_reflex.utils.tracing import create_tracer, reset_tracer


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


def _op() -> GenerateOpResp:
    return GenerateOpResp(
        op_type="sql",
        op_content="SELECT channel, SUM(amount) AS amount_sum FROM orders GROUP BY channel",
        format_valid=True,
    )


def _state(*, op: GenerateOpResp | None = None, sub_id: str = "st-1") -> dict[str, Any]:
    state: dict[str, Any] = dict(
        create_initial_task_state(
            dataset_id="ds-1",
            user_query="渠道对比",
            task_id="t-exec",
            strategy_flags=StrategyFlags(),
        )
    )
    state["current_op"] = op if op is not None else _op()
    state["current_sub_task_id"] = sub_id
    return state


def test_mock_success_returns_result_rows() -> None:
    mock = MockSandboxExecutor(MockScenario.SUCCESS)
    node = NodeExecute(executor=mock)
    delta = node.run(_state())  # type: ignore[arg-type]
    assert delta["next_node"] == "result_validate"
    resp = delta["execute_resp"]
    assert resp.success is True
    assert resp.result_rows
    assert resp.result_columns
    results = delta["sub_task_results"]
    assert len(results) == 1
    assert results[0].status == "success"
    assert results[0].result_rows == resp.result_rows
    assert mock.last_req is not None
    assert mock.last_req.dataset_id == "ds-1"


def test_mock_timeout_sets_resource_killed_and_marks_failed() -> None:
    mock = MockSandboxExecutor(MockScenario.TIMEOUT)
    node = NodeExecute(executor=mock)
    delta = node.run(_state())  # type: ignore[arg-type]
    assert delta["next_node"] == "error_classify"
    resp = delta["execute_resp"]
    assert resp.success is False
    assert resp.resource_killed is True
    results = delta["sub_task_results"]
    assert results[0].status == "failed"
    assert results[0].error_summary


def test_resource_limit_also_marks_failed() -> None:
    mock = MockSandboxExecutor(MockScenario.RESOURCE_LIMIT)
    node = NodeExecute(executor=mock)
    delta = node.run(_state())  # type: ignore[arg-type]
    assert delta["execute_resp"].resource_killed is True
    assert delta["sub_task_results"][0].status == "failed"
    assert delta["next_node"] == "error_classify"


def test_soft_failure_routes_to_error_classify_without_failed_mark() -> None:
    mock = MockSandboxExecutor(MockScenario.SYNTAX_ERROR)
    node = NodeExecute(executor=mock)
    delta = node.run(_state())  # type: ignore[arg-type]
    assert delta["next_node"] == "error_classify"
    assert delta["execute_resp"].success is False
    assert delta["execute_resp"].resource_killed is False
    assert delta["sub_task_results"] == []


class _UnavailableExecutor:
    def execute(self, req: ExecuteReq) -> ExecuteResp:
        raise SandboxUnavailableError("docker daemon unavailable (test)")


def test_sandbox_unavailable_propagates_no_host_fallback() -> None:
    node = NodeExecute(executor=_UnavailableExecutor(), max_attempts=1)
    with pytest.raises(SandboxUnavailableError, match="docker daemon unavailable"):
        node.run(_state())  # type: ignore[arg-type]


def test_missing_op_reports() -> None:
    node = NodeExecute(executor=MockSandboxExecutor())
    state = _state()
    state["current_op"] = None
    state.pop("generated_ops", None)
    delta = node.run(state)  # type: ignore[arg-type]
    assert delta["next_node"] == "report"
    assert delta["execute_resp"].success is False
