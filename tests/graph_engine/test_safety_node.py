# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

import pytest

from data_reflex.graph_engine.nodes.safety_check import NodeSafetyCheck
from data_reflex.models.schemas import GenerateOpResp
from data_reflex.models.state import StrategyFlags, create_initial_task_state
from data_reflex.utils.tracing import create_tracer, reset_tracer


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


def _state(
    *,
    op: GenerateOpResp | None,
    enable_human_confirm: bool = True,
) -> dict[str, Any]:
    state: dict[str, Any] = dict(
        create_initial_task_state(
            dataset_id="ds-1",
            user_query="渠道对比",
            task_id="t-safe",
            strategy_flags=StrategyFlags(enable_human_confirm=enable_human_confirm),
        )
    )
    state["current_op"] = op
    return state


def test_safe_select_routes_to_execute() -> None:
    node = NodeSafetyCheck()
    op = GenerateOpResp(
        op_type="sql",
        op_content="SELECT channel, SUM(amount) AS amount_sum FROM orders GROUP BY channel",
        format_valid=True,
    )
    delta = node.run(_state(op=op))  # type: ignore[arg-type]
    assert delta["next_node"] == "execute"
    assert delta["safety_check"].passed is True


def test_dangerous_write_blocked_to_report() -> None:
    node = NodeSafetyCheck()
    op = GenerateOpResp(
        op_type="sql",
        op_content="DELETE FROM orders WHERE channel = 'APP'",
        format_valid=True,
    )
    delta = node.run(_state(op=op))  # type: ignore[arg-type]
    assert delta["next_node"] == "report"
    assert delta["safety_check"].passed is False
    assert delta["safety_check"].violation_type == "dangerous_write"
    assert delta["safety_check"].violation_detail


def test_unauthorized_column_blocked() -> None:
    node = NodeSafetyCheck()
    op = GenerateOpResp(
        op_type="sql",
        op_content="SELECT order_id, phone FROM orders",
        format_valid=True,
    )
    delta = node.run(_state(op=op))  # type: ignore[arg-type]
    assert delta["next_node"] == "report"
    assert delta["safety_check"].violation_type == "unauthorized_column"


def test_dangerous_python_blocked() -> None:
    node = NodeSafetyCheck()
    op = GenerateOpResp(
        op_type="python",
        op_content="import os\nos.system('rm -rf /')\n",
        format_valid=True,
    )
    delta = node.run(_state(op=op))  # type: ignore[arg-type]
    assert delta["next_node"] == "report"
    assert delta["safety_check"].passed is False
    assert delta["safety_check"].violation_type == "dangerous_write"


def test_human_confirm_off_still_blocks_danger() -> None:
    """Closing human confirm must not open a bypass for dangerous ops."""
    node = NodeSafetyCheck()
    op = GenerateOpResp(
        op_type="sql",
        op_content="DROP TABLE orders",
        format_valid=True,
    )
    delta = node.run(_state(op=op, enable_human_confirm=False))  # type: ignore[arg-type]
    assert delta["next_node"] == "report"
    assert delta["safety_check"].violation_type == "dangerous_write"


def test_missing_op_reports() -> None:
    node = NodeSafetyCheck()
    delta = node.run(_state(op=None))  # type: ignore[arg-type]
    assert delta["next_node"] == "report"
    assert delta["safety_check"].passed is False
