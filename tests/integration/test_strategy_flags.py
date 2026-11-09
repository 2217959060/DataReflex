# -*- coding: utf-8 -*-
"""T38: strategy-flag ablation on one shared eval case (Mock only)."""

from __future__ import annotations

from typing import Any

import pytest

from data_reflex.graph_engine.builder import (
    build_main_graph,
    default_nodes,
    make_thread_config,
    open_sqlite_checkpointer,
)
from data_reflex.graph_engine.nodes.base import BaseNode
from data_reflex.graph_engine.nodes.execute import NodeExecute
from data_reflex.models.schemas import ExecuteReq, ExecuteResp, GenerateOpResp
from data_reflex.models.state import StrategyFlags, create_initial_task_state
from data_reflex.modules.executor.mock_executor import MockSandboxExecutor, MockScenario
from data_reflex.utils.tracing import create_tracer, reset_tracer

# Shared ablation case — same NL query / dataset for every flag matrix cell.
_CASE_QUERY = "对比 APP 和官网 渠道销售额"
_CASE_DATASET = "ds-ablation"


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


def _flags(**overrides: bool) -> StrategyFlags:
    base = {
        "enable_planner": True,
        "enable_reflector": True,
        "enable_sandbox": True,
        "enable_human_confirm": False,
        "enable_fallback_report": True,
    }
    base.update(overrides)
    return StrategyFlags.model_validate(base)


def _seed(task_id: str, flags: StrategyFlags) -> dict[str, Any]:
    return dict(
        create_initial_task_state(
            dataset_id=_CASE_DATASET,
            user_query=_CASE_QUERY,
            task_id=task_id,
            strategy_flags=flags,
        )
    )


def _invoke(
    *,
    task_id: str,
    flags: StrategyFlags,
    execute: Any | None = None,
    generate_op: Any | None = None,
) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if execute is not None:
        overrides["execute"] = execute
    else:
        overrides["execute"] = NodeExecute(
            executor=MockSandboxExecutor(MockScenario.SUCCESS)
        )
    if generate_op is not None:
        overrides["generate_op"] = generate_op
    nodes = default_nodes(**overrides)
    with open_sqlite_checkpointer(":memory:") as cp:
        app = build_main_graph(nodes=nodes, checkpointer=cp)
        return app.invoke(_seed(task_id, flags), config=make_thread_config(task_id))


class _AlwaysFailExecutor:
    def __init__(self) -> None:
        self.calls = 0
        self._inner = MockSandboxExecutor(MockScenario.SYNTAX_ERROR)

    def execute(self, req: ExecuteReq) -> ExecuteResp:
        self.calls += 1
        return self._inner.execute(req)


class _FlakyThenOkExecutor:
    def __init__(self) -> None:
        self.calls = 0
        self._fail = MockSandboxExecutor(MockScenario.SYNTAX_ERROR)
        self._ok = MockSandboxExecutor(MockScenario.SUCCESS)

    def execute(self, req: ExecuteReq) -> ExecuteResp:
        self.calls += 1
        if self.calls == 1:
            return self._fail.execute(req)
        return self._ok.execute(req)


class _DangerGenerateOp(BaseNode):
    name = "Node_GenerateOp"

    def execute(self, state: Any) -> dict[str, Any]:
        op = GenerateOpResp(
            op_type="sql",
            op_content="DELETE FROM orders WHERE channel = 'APP'",
            format_valid=True,
        )
        return {
            "generated_ops": {"st-danger": op},
            "current_op": op,
            "current_sub_task_id": "st-danger",
            "next_node": "safety_check",
        }


# ---------------------------------------------------------------------------
# Planner on / off — same case
# ---------------------------------------------------------------------------


def test_ablation_planner_on_multistep() -> None:
    out = _invoke(task_id="ab-plan-on", flags=_flags(enable_planner=True))
    plan = out.get("plan")
    assert plan is not None and plan.success is True
    assert len(plan.sub_tasks) >= 2
    assert out.get("final_report") is not None


def test_ablation_planner_off_single_step() -> None:
    out = _invoke(task_id="ab-plan-off", flags=_flags(enable_planner=False))
    plan = out.get("plan")
    assert plan is not None and plan.success is True
    assert len(plan.sub_tasks) == 1
    assert plan.sub_tasks[0].task_id == "st-probe"
    assert plan.sub_tasks[0].expected_input.get("single_step") is True
    assert out.get("final_report") is not None


# ---------------------------------------------------------------------------
# Reflector on / off — same case, failing executor
# ---------------------------------------------------------------------------


def test_ablation_reflector_on_retries() -> None:
    flaky = _FlakyThenOkExecutor()
    out = _invoke(
        task_id="ab-refl-on",
        flags=_flags(enable_reflector=True),
        execute=NodeExecute(executor=flaky),
    )
    assert flaky.calls >= 2
    assert out.get("final_report") is not None
    assert any(r.status == "success" for r in (out.get("sub_task_results") or []))


def test_ablation_reflector_off_no_retry() -> None:
    failing = _AlwaysFailExecutor()
    out = _invoke(
        task_id="ab-refl-off",
        flags=_flags(enable_reflector=False),
        execute=NodeExecute(executor=failing),
    )
    # Two-step channel_compare at most once each — no heal loop.
    assert failing.calls >= 1
    assert failing.calls <= 2
    assert out.get("reflect") is None
    assert not any((out.get("retry_counts") or {}).values())
    assert out.get("final_report") is not None
    assert all(r.status != "success" for r in (out.get("sub_task_results") or []))


# ---------------------------------------------------------------------------
# Human confirm on / off — same case; danger still blocked when off
# ---------------------------------------------------------------------------


def test_ablation_human_confirm_on_waits() -> None:
    out = _invoke(
        task_id="ab-hc-on",
        flags=_flags(enable_human_confirm=True),
    )
    assert out.get("status") == "waiting_confirm"
    assert out.get("final_report") is None


def test_ablation_human_confirm_off_no_interrupt() -> None:
    out = _invoke(
        task_id="ab-hc-off",
        flags=_flags(enable_human_confirm=False),
    )
    assert out.get("status") != "waiting_confirm"
    assert out.get("final_report") is not None


def test_ablation_human_confirm_off_danger_still_blocked() -> None:
    out = _invoke(
        task_id="ab-hc-danger",
        flags=_flags(enable_human_confirm=False),
        generate_op=_DangerGenerateOp(),
    )
    assert out.get("status") != "waiting_confirm"
    safety = out.get("safety_check")
    assert safety is not None
    assert safety.passed is False
    assert safety.violation_type == "dangerous_write"
    assert out.get("execute_resp") is None or not getattr(
        out.get("execute_resp"), "success", False
    )


def test_ablation_matrix_same_query_dataset() -> None:
    """Sanity: every ablation cell above uses the locked case identity."""
    assert _CASE_QUERY
    assert _CASE_DATASET == "ds-ablation"
