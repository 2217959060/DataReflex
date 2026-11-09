# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

import pytest

from data_reflex.graph_engine.builder import (
    build_main_graph,
    default_nodes,
    make_thread_config,
    open_sqlite_checkpointer,
)
from data_reflex.graph_engine.nodes.execute import NodeExecute
from data_reflex.models.schemas import ExecuteReq, ExecuteResp
from data_reflex.models.state import StrategyFlags, create_initial_task_state
from data_reflex.modules.executor.mock_executor import MockSandboxExecutor, MockScenario
from data_reflex.utils.tracing import create_tracer, reset_tracer


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


def _seed(
    *,
    query: str,
    task_id: str,
    model_unavailable: bool = False,
) -> dict[str, Any]:
    state: dict[str, Any] = dict(
        create_initial_task_state(
            dataset_id="ds-demo",
            user_query=query,
            task_id=task_id,
            strategy_flags=StrategyFlags(
                enable_human_confirm=False,
                enable_fallback_report=True,
                enable_reflector=True,
            ),
        )
    )
    if model_unavailable:
        state["model_unavailable"] = True
    return state


class _FlakyThenOkExecutor:
    """First call fails (syntax); subsequent calls succeed — drives Reflect heal."""

    def __init__(self) -> None:
        self.calls = 0
        self._fail = MockSandboxExecutor(MockScenario.SYNTAX_ERROR)
        self._ok = MockSandboxExecutor(MockScenario.SUCCESS)

    def execute(self, req: ExecuteReq) -> ExecuteResp:
        self.calls += 1
        if self.calls == 1:
            return self._fail.execute(req)
        return self._ok.execute(req)


def test_path_plan_execute_success_report() -> None:
    nodes = default_nodes(
        execute=NodeExecute(executor=MockSandboxExecutor(MockScenario.SUCCESS))
    )
    with open_sqlite_checkpointer(":memory:") as cp:
        app = build_main_graph(nodes=nodes, checkpointer=cp)
        out = app.invoke(
            _seed(query="对比 APP 和官网 渠道销售额", task_id="t-ok"),
            config=make_thread_config("t-ok"),
        )
    assert out.get("final_report") is not None
    assert out["final_report"].summary
    assert any(r.status == "success" for r in out.get("sub_task_results") or [])
    assert out.get("next_node") in {"end", None} or out.get("status") in {
        "success",
        "running",
        "fallback",
    }


def test_path_execute_fail_heal_success_report() -> None:
    flaky = _FlakyThenOkExecutor()
    nodes = default_nodes(execute=NodeExecute(executor=flaky))
    with open_sqlite_checkpointer(":memory:") as cp:
        app = build_main_graph(nodes=nodes, checkpointer=cp)
        out = app.invoke(
            _seed(query="对比 APP 和官网 渠道销售额", task_id="t-heal"),
            config=make_thread_config("t-heal"),
        )
    assert flaky.calls >= 2
    assert out.get("final_report") is not None
    assert any(r.status == "success" for r in out.get("sub_task_results") or [])


def test_path_model_unavailable_fallback_report() -> None:
    nodes = default_nodes(
        execute=NodeExecute(executor=MockSandboxExecutor(MockScenario.SUCCESS))
    )
    with open_sqlite_checkpointer(":memory:") as cp:
        app = build_main_graph(nodes=nodes, checkpointer=cp)
        # Out-of-scope short-circuits to Report; model_unavailable → Fallback.
        out = app.invoke(
            _seed(
                query="请帮我训练一个神经网络预测房价",
                task_id="t-fb",
                model_unavailable=True,
            ),
            config=make_thread_config("t-fb"),
        )
    assert out.get("fallback_report") is not None
    assert out["fallback_report"].is_analysis_conclusion is False
    assert "静态诊断" in out["fallback_report"].summary
    assert out.get("status") == "fallback"


def test_sqlite_checkpointer_persists_thread() -> None:
    nodes = default_nodes(
        execute=NodeExecute(executor=MockSandboxExecutor(MockScenario.SUCCESS))
    )
    with open_sqlite_checkpointer(":memory:") as cp:
        app = build_main_graph(nodes=nodes, checkpointer=cp)
        cfg = make_thread_config("t-ckpt")
        app.invoke(
            _seed(query="对比 APP 和官网 渠道销售额", task_id="t-ckpt"),
            config=cfg,
        )
        snap = app.get_state(cfg)
        assert snap.values.get("final_report") is not None
