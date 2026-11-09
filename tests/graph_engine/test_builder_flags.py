# -*- coding: utf-8 -*-
"""T33: strategy-flag branches on the shared main graph."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from data_reflex.config.settings import Settings
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
from data_reflex.modules.executor.docker_executor import DockerSandboxExecutor
from data_reflex.modules.executor.mock_executor import MockSandboxExecutor, MockScenario
from data_reflex.utils.tracing import create_tracer, reset_tracer

_QUERY = "对比 APP 和官网 渠道销售额"


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


def _seed(
    *,
    task_id: str,
    query: str = _QUERY,
    flags: StrategyFlags | None = None,
) -> dict[str, Any]:
    return dict(
        create_initial_task_state(
            dataset_id="ds-demo",
            user_query=query,
            task_id=task_id,
            strategy_flags=flags
            or StrategyFlags(
                enable_human_confirm=False,
                enable_fallback_report=True,
            ),
        )
    )


class _AlwaysFailExecutor:
    def __init__(self) -> None:
        self.calls = 0
        self._inner = MockSandboxExecutor(MockScenario.SYNTAX_ERROR)

    def execute(self, req: ExecuteReq) -> ExecuteResp:
        self.calls += 1
        return self._inner.execute(req)


class _DangerGenerateOp(BaseNode):
    """Force a dangerous write so SafetyCheck can prove human_confirm is not a bypass."""

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


class _FakeContainer:
    def __init__(self) -> None:
        self.id = "cid-flag"
        self.killed = False
        self.removed = False

    def wait(self, timeout: int | None = None) -> dict[str, int]:
        return {"StatusCode": 0}

    def logs(self, stdout: bool = True, stderr: bool = True) -> bytes:
        return (
            b'__DATA_REFLEX_RESULT__{"columns":["channel"],"rows":[{"channel":"APP"}]}'
        )

    def kill(self) -> None:
        self.killed = True

    def remove(self, force: bool = False) -> None:
        self.removed = True


def _mock_docker_client() -> MagicMock:
    client = MagicMock()
    client.ping.return_value = True
    client.images.get.return_value = MagicMock()
    client.containers.run.return_value = _FakeContainer()
    return client


def test_planner_off_uses_single_step_probe() -> None:
    nodes = default_nodes(
        execute=NodeExecute(executor=MockSandboxExecutor(MockScenario.SUCCESS))
    )
    flags = StrategyFlags(
        enable_planner=False,
        enable_human_confirm=False,
        enable_reflector=True,
    )
    with open_sqlite_checkpointer(":memory:") as cp:
        app = build_main_graph(nodes=nodes, checkpointer=cp)
        out = app.invoke(
            _seed(task_id="t-plan-off", flags=flags),
            config=make_thread_config("t-plan-off"),
        )
    plan = out.get("plan")
    assert plan is not None
    assert plan.success is True
    assert len(plan.sub_tasks) == 1
    assert plan.sub_tasks[0].task_id == "st-probe"
    assert plan.sub_tasks[0].expected_input.get("single_step") is True
    assert out.get("final_report") is not None


def test_reflector_off_failure_goes_straight_to_report() -> None:
    failing = _AlwaysFailExecutor()
    nodes = default_nodes(execute=NodeExecute(executor=failing))
    flags = StrategyFlags(
        enable_planner=True,
        enable_human_confirm=False,
        enable_reflector=False,
    )
    with open_sqlite_checkpointer(":memory:") as cp:
        app = build_main_graph(nodes=nodes, checkpointer=cp)
        out = app.invoke(
            _seed(task_id="t-refl-off", flags=flags),
            config=make_thread_config("t-refl-off"),
        )
    # No heal loop: ErrorClassify skips Reflect; execute once per subtask at most.
    assert failing.calls >= 1
    assert failing.calls <= 2  # two-step channel_compare template, no retries
    assert out.get("final_report") is not None
    assert out.get("reflect") is None
    assert all(r.status != "success" for r in (out.get("sub_task_results") or []))
    assert not any((out.get("retry_counts") or {}).values())


def test_human_confirm_off_no_interrupt_and_danger_still_blocked() -> None:
    nodes = default_nodes(
        generate_op=_DangerGenerateOp(),
        execute=NodeExecute(executor=MockSandboxExecutor(MockScenario.SUCCESS)),
    )
    flags = StrategyFlags(
        enable_human_confirm=False,
        enable_planner=True,
        enable_reflector=True,
    )
    with open_sqlite_checkpointer(":memory:") as cp:
        app = build_main_graph(nodes=nodes, checkpointer=cp)
        out = app.invoke(
            _seed(task_id="t-hc-off", flags=flags),
            config=make_thread_config("t-hc-off"),
        )
    assert out.get("status") != "waiting_confirm"
    safety = out.get("safety_check")
    assert safety is not None
    assert safety.passed is False
    assert safety.violation_type == "dangerous_write"
    assert out.get("execute_resp") is None or not getattr(
        out.get("execute_resp"), "success", False
    )


def test_sandbox_off_still_oneshot_container(tmp_path: Path) -> None:
    client = _mock_docker_client()
    settings = Settings.model_validate(
        {
            "enable_sandbox": False,
            "sandbox_image": "data-reflex-sandbox:3.11",
            "sqlite_path": "data/data_reflex.db",
        }
    )
    exe = DockerSandboxExecutor(
        settings=settings,
        dataset_root=tmp_path / "datasets",
        client=client,
        network_disabled=True,
    )
    nodes = default_nodes(execute=NodeExecute(executor=exe, settings=settings))
    flags = StrategyFlags(
        enable_sandbox=False,
        enable_human_confirm=False,
        enable_planner=True,
    )
    with open_sqlite_checkpointer(":memory:") as cp:
        app = build_main_graph(nodes=nodes, checkpointer=cp)
        out = app.invoke(
            _seed(task_id="t-sbx-off", flags=flags),
            config=make_thread_config("t-sbx-off"),
        )
    assert client.containers.run.called
    kwargs = exe.last_run_kwargs
    assert kwargs is not None
    assert kwargs["network_disabled"] is True
    assert kwargs["detach"] is True
    # One-shot cleanup still happened.
    container = client.containers.run.return_value
    assert container.removed is True
    assert out.get("final_report") is not None


def test_planner_on_still_multistep() -> None:
    """Control: shared graph with planner on keeps ≥2 subtasks (same eval path)."""
    nodes = default_nodes(
        execute=NodeExecute(executor=MockSandboxExecutor(MockScenario.SUCCESS))
    )
    flags = StrategyFlags(enable_planner=True, enable_human_confirm=False)
    with open_sqlite_checkpointer(":memory:") as cp:
        app = build_main_graph(nodes=nodes, checkpointer=cp)
        out = app.invoke(
            _seed(task_id="t-plan-on", flags=flags),
            config=make_thread_config("t-plan-on"),
        )
    assert out["plan"] is not None
    assert len(out["plan"].sub_tasks) >= 2
