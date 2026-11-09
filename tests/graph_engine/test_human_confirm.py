# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

import pytest

from data_reflex.graph_engine.nodes.human_confirm import NodeHumanConfirmPlan
from data_reflex.models.schemas import SubTaskResult
from data_reflex.models.state import (
    ConfirmReq,
    StrategyFlags,
    create_initial_task_state,
)
from data_reflex.modules.human_confirm.gate import PlanConfirmGate
from data_reflex.utils.tracing import create_tracer, reset_tracer


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


def _state(
    *,
    task_id: str = "t-confirm",
    enable_human_confirm: bool = True,
    with_result: bool = True,
) -> Any:
    state = create_initial_task_state(
        dataset_id="ds-1",
        user_query="渠道对比",
        task_id=task_id,
        strategy_flags=StrategyFlags(enable_human_confirm=enable_human_confirm),
    )
    if with_result:
        state["sub_task_results"] = [
            SubTaskResult(
                sub_task_id="st-1",
                status="success",
                result_rows=[{"channel": "APP", "amount": 1}],
                result_columns=["channel", "amount"],
            )
        ]
    return state


def test_skip_when_human_confirm_disabled() -> None:
    node = NodeHumanConfirmPlan(confirm_gate=PlanConfirmGate(timeout_seconds=300))
    delta = node.run(_state(enable_human_confirm=False))
    assert delta["next_node"] == "generate_op"
    assert delta["status"] == "running"
    assert len(delta["sub_task_results"]) == 1


def test_accept_continues_to_generate_op() -> None:
    clock = {"t": 0.0}
    gate = PlanConfirmGate(timeout_seconds=300, time_fn=lambda: clock["t"])
    node = NodeHumanConfirmPlan(confirm_gate=gate)
    state = _state(task_id="t-accept")

    waiting = node.run(state)
    assert waiting["status"] == "waiting_confirm"
    assert "next_node" not in waiting

    rec = node.apply_confirm(
        ConfirmReq(
            task_id="t-accept",
            confirm_type="plan",
            decision="accept",
            idempotency_key="key-accept-1",
        )
    )
    assert rec.mutated is True
    assert rec.decision == "accept"

    state = {**state, **waiting}
    done = node.run(state)  # type: ignore[arg-type]
    assert done["next_node"] == "generate_op"
    assert done["status"] == "running"
    assert done["sub_task_results"][0].sub_task_id == "st-1"


def test_reject_routes_to_report_keeps_results() -> None:
    gate = PlanConfirmGate(timeout_seconds=300)
    node = NodeHumanConfirmPlan(confirm_gate=gate)
    state = _state(task_id="t-reject")
    node.run(state)
    node.apply_confirm(
        ConfirmReq(
            task_id="t-reject",
            confirm_type="plan",
            decision="reject",
            idempotency_key="key-reject-1",
        )
    )
    done = node.run(state)
    assert done["next_node"] == "report"
    assert len(done["sub_task_results"]) == 1
    assert done["sub_task_results"][0].status == "success"


def test_timeout_routes_to_report() -> None:
    clock = {"t": 0.0}
    gate = PlanConfirmGate(timeout_seconds=10, time_fn=lambda: clock["t"])
    node = NodeHumanConfirmPlan(confirm_gate=gate)
    state = _state(task_id="t-timeout")
    waiting = node.run(state)
    assert waiting["status"] == "waiting_confirm"

    clock["t"] = 11.0  # past timeout
    done = node.run({**state, **waiting})  # type: ignore[arg-type]
    assert done["next_node"] == "report"
    assert len(done["sub_task_results"]) == 1
    assert gate.mutation_count("t-timeout") == 1


def test_duplicate_confirm_does_not_mutate_again() -> None:
    gate = PlanConfirmGate(timeout_seconds=300)
    node = NodeHumanConfirmPlan(confirm_gate=gate)
    state = _state(task_id="t-idem")
    node.run(state)

    req = ConfirmReq(
        task_id="t-idem",
        confirm_type="plan",
        decision="accept",
        idempotency_key="same-key",
    )
    first = node.apply_confirm(req)
    assert first.mutated is True
    assert gate.mutation_count("t-idem") == 1

    second = node.apply_confirm(req)
    assert second.mutated is False
    assert second.decision == "accept"
    assert gate.mutation_count("t-idem") == 1

    # Different key after resolve must not flip decision or bump mutations.
    flipped = node.apply_confirm(
        ConfirmReq(
            task_id="t-idem",
            confirm_type="plan",
            decision="reject",
            idempotency_key="other-key",
        )
    )
    assert flipped.mutated is False
    assert flipped.decision == "accept"
    assert gate.mutation_count("t-idem") == 1

    done = node.run(state)
    assert done["next_node"] == "generate_op"
