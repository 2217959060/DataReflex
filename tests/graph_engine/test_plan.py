# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Any

import pytest

from data_reflex.graph_engine.nodes.plan import NodePlan
from data_reflex.models.schemas import IntentParseResp, PlanReq, SubTask
from data_reflex.models.state import create_initial_task_state
from data_reflex.modules.plan.builder import PlanBuilder
from data_reflex.utils.tracing import create_tracer, reset_tracer


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


@pytest.fixture()
def node() -> NodePlan:
    return NodePlan()


def _intent(
    *,
    tag: str = "channel_compare",
    query: str = "各渠道销售额对比",
    constraints: list[str] | None = None,
) -> IntentParseResp:
    cons = constraints if constraints is not None else ["channel IS NOT NULL"]
    return IntentParseResp(
        in_scope=True,
        need_clarification=False,
        question_tag=tag,  # type: ignore[arg-type]
        normalized_query=f"tag={tag} | query={query}",
        constraints=cons,
    )


def test_multistep_plan_has_at_least_two_subtasks_with_deps(node: NodePlan) -> None:
    state = create_initial_task_state(
        dataset_id="ds-1",
        user_query="各渠道销售额对比",
        task_id="t-plan-1",
    )
    state["intent"] = _intent()
    delta = node.run(state)
    plan = delta["plan"]
    assert plan.success is True
    assert len(plan.sub_tasks) >= 2
    # At least one dependency edge.
    assert any(st.depends_on for st in plan.sub_tasks)
    # Dependency graph is JSON-serializable (acceptance).
    payload = [st.model_dump() for st in plan.sub_tasks]
    encoded = json.dumps(payload, ensure_ascii=False)
    restored = json.loads(encoded)
    assert len(restored) == len(plan.sub_tasks)
    assert restored[1]["depends_on"] == plan.sub_tasks[1].depends_on


def test_return_rate_plan_joins_then_aggregates(node: NodePlan) -> None:
    state = create_initial_task_state(
        dataset_id="ds-1",
        user_query="退货率 Top5",
        task_id="t-rr",
    )
    state["intent"] = _intent(
        tag="return_rate_topn",
        query="退货率 Top5",
        constraints=["top_n=5"],
    )
    plan = node.run(state)["plan"]
    assert plan.success is True
    assert len(plan.sub_tasks) >= 2
    assert plan.sub_tasks[0].parallel_group == 0
    assert plan.sub_tasks[1].depends_on == [plan.sub_tasks[0].task_id]
    assert "orders" in plan.sub_tasks[0].expected_input.get("tables", [])
    assert "returns" in plan.sub_tasks[0].expected_input.get("tables", [])


def test_all_six_tags_produce_multistep_plans() -> None:
    builder = PlanBuilder()
    tags = [
        "return_rate_topn",
        "channel_compare",
        "time_trend",
        "segment_compare",
        "promo_compare",
        "repurchase_rate",
    ]
    for tag in tags:
        resp = builder.build(
            PlanReq(
                dataset_id="ds",
                normalized_query=f"tag={tag} | query=demo",
                constraints=[],
            ),
            question_tag=tag,
        )
        assert resp.success is True, tag
        assert len(resp.sub_tasks) >= 2, tag
        assert all(isinstance(st, SubTask) for st in resp.sub_tasks)


def test_channel_plan_marks_reuse_and_hides_intermediate() -> None:
    resp = PlanBuilder().build(
        PlanReq(
            dataset_id="ds",
            normalized_query="tag=channel_compare | query=demo",
            constraints=[],
        ),
        question_tag="channel_compare",
    )
    assert resp.success is True
    agg, compare = resp.sub_tasks[0], resp.sub_tasks[1]
    assert agg.expected_output.get("report") is False
    assert compare.expected_input.get("reuse_result") is True
    assert compare.expected_output.get("report") is True
    assert compare.expected_output.get("chart") == "bar"


def test_channel_compare_marks_reuse_and_hides_intermediate() -> None:
    resp = PlanBuilder().build(
        PlanReq(
            dataset_id="ds",
            normalized_query="tag=channel_compare | query=demo",
            constraints=[],
        ),
        question_tag="channel_compare",
    )
    assert resp.sub_tasks[0].expected_output.get("report") is False
    assert resp.sub_tasks[1].expected_input.get("reuse_result") is True
    assert resp.sub_tasks[1].expected_output.get("report") is not False


def test_plan_fails_without_intent(node: NodePlan) -> None:
    state = create_initial_task_state(
        dataset_id="ds-1", user_query="渠道对比", task_id="t-no-intent"
    )
    plan = node.run(state)["plan"]
    assert plan.success is False
    assert plan.error_msg is not None
    assert "intent" in plan.error_msg


def test_plan_fails_when_clarification_needed(node: NodePlan) -> None:
    state = create_initial_task_state(
        dataset_id="ds-1", user_query="再按渠道", task_id="t-clarify"
    )
    state["intent"] = IntentParseResp(
        in_scope=True,
        need_clarification=True,
        clarification_questions=["请说明指标"],
        question_tag=None,
        normalized_query=None,
    )
    plan = node.run(state)["plan"]
    assert plan.success is False
    assert "clarification" in (plan.error_msg or "").lower() or "澄清" in (
        plan.error_msg or ""
    )


def test_plan_fails_out_of_scope(node: NodePlan) -> None:
    state = create_initial_task_state(
        dataset_id="ds-1", user_query="预测", task_id="t-oos"
    )
    state["intent"] = IntentParseResp(
        in_scope=False,
        reject_reason="预测不支持",
        need_clarification=False,
    )
    plan = node.run(state)["plan"]
    assert plan.success is False
    assert plan.sub_tasks == []
