# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

import pytest

from data_reflex.graph_engine.nodes.plan_validate import NodePlanValidate
from data_reflex.models.schemas import PlanReq, PlanResp, SubTask
from data_reflex.models.state import StrategyFlags, create_initial_task_state
from data_reflex.modules.plan.builder import PlanBuilder
from data_reflex.modules.plan_validate.validator import PlanValidator, detect_cycle
from data_reflex.utils.tracing import create_tracer, reset_tracer


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


@pytest.fixture()
def node() -> NodePlanValidate:
    return NodePlanValidate()


def _st(
    task_id: str,
    depends_on: list[str] | None = None,
    **expected_input: Any,
) -> SubTask:
    return SubTask(
        task_id=task_id,
        description=f"task {task_id}",
        depends_on=list(depends_on or []),
        expected_input=dict(expected_input),
        expected_output={},
        parallel_group=0,
    )


def test_cycle_a_b_a_detected(node: NodePlanValidate) -> None:
    """Acceptance: construct A→B→A → has_cycle=True, next_node=report."""
    plan = PlanResp(
        success=True,
        sub_tasks=[
            _st("A", depends_on=["B"], tables=["orders"]),
            _st("B", depends_on=["A"], tables=["orders"]),
        ],
    )
    state = create_initial_task_state(
        dataset_id="ds-1", user_query="q", task_id="t-cycle"
    )
    state["plan"] = plan
    delta = node.run(state)
    assert delta["next_node"] == "report"
    assert delta["plan"].has_cycle is True
    assert delta["plan"].cycle_detail is not None
    assert "A" in delta["plan"].cycle_detail and "B" in delta["plan"].cycle_detail
    assert delta["plan"].success is False


def test_detect_cycle_helper_direct() -> None:
    tasks = [
        _st("A", depends_on=["B"]),
        _st("B", depends_on=["A"]),
    ]
    detail = detect_cycle(tasks)
    assert detail is not None
    assert detail.startswith("A") or "A" in detail


def test_missing_depends_on_target(node: NodePlanValidate) -> None:
    plan = PlanResp(
        success=True,
        sub_tasks=[
            _st("st-1", tables=["orders"]),
            _st("st-2", depends_on=["st-missing"], tables=["orders"]),
        ],
    )
    state = create_initial_task_state(
        dataset_id="ds-1", user_query="q", task_id="t-miss"
    )
    state["plan"] = plan
    delta = node.run(state)
    assert delta["next_node"] == "report"
    assert delta["plan"].missing_inputs
    assert any("st-missing" in m for m in delta["plan"].missing_inputs)
    assert delta["plan"].success is False


def test_missing_from_sub_task_not_declared(node: NodePlanValidate) -> None:
    plan = PlanResp(
        success=True,
        sub_tasks=[
            _st("st-1", tables=["orders"]),
            _st(
                "st-2",
                depends_on=[],  # forgot depends_on
                tables=["orders"],
                from_sub_task="st-1",
            ),
        ],
    )
    state = create_initial_task_state(
        dataset_id="ds-1", user_query="q", task_id="t-from"
    )
    state["plan"] = plan
    delta = node.run(state)
    assert delta["next_node"] == "report"
    assert delta["plan"].missing_inputs


def test_out_of_scope_table_routes_to_report(node: NodePlanValidate) -> None:
    plan = PlanResp(
        success=True,
        sub_tasks=[_st("st-1", tables=["inventory"])],
    )
    state = create_initial_task_state(
        dataset_id="ds-1", user_query="q", task_id="t-oos"
    )
    state["plan"] = plan
    delta = node.run(state)
    assert delta["next_node"] == "report"
    assert any("inventory" in m for m in delta["plan"].missing_inputs)


def test_valid_builder_plan_routes_to_human_confirm(node: NodePlanValidate) -> None:
    built = PlanBuilder().build(
        PlanReq(
            dataset_id="ds-1",
            normalized_query="tag=channel_compare | query=渠道对比",
            constraints=[],
        ),
        question_tag="channel_compare",
    )
    assert built.success is True
    state = create_initial_task_state(
        dataset_id="ds-1",
        user_query="渠道对比",
        task_id="t-ok",
        strategy_flags=StrategyFlags(enable_human_confirm=True),
    )
    state["plan"] = built
    delta = node.run(state)
    assert delta["next_node"] == "human_confirm"
    assert delta["plan"].success is True
    assert delta["plan"].has_cycle is False
    assert delta["plan"].missing_inputs == []


def test_valid_plan_skips_confirm_when_disabled(node: NodePlanValidate) -> None:
    built = PlanBuilder().build(
        PlanReq(
            dataset_id="ds-1",
            normalized_query="tag=promo_compare | query=促销对比",
        ),
        question_tag="promo_compare",
    )
    state = create_initial_task_state(
        dataset_id="ds-1",
        user_query="促销对比",
        task_id="t-noconfirm",
        strategy_flags=StrategyFlags(enable_human_confirm=False),
    )
    state["plan"] = built
    delta = node.run(state)
    assert delta["next_node"] == "generate_op"


def test_validator_unit_empty_plan() -> None:
    outcome = PlanValidator().validate(
        PlanResp(success=False, sub_tasks=[], error_msg="boom")
    )
    assert outcome.next_node == "report"
