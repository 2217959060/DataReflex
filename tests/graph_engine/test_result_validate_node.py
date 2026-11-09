# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

import pytest

from data_reflex.config.settings import Settings
from data_reflex.graph_engine.nodes.result_validate import NodeResultValidate
from data_reflex.models.schemas import ExecuteResp
from data_reflex.models.state import StrategyFlags, create_initial_task_state
from data_reflex.utils.tracing import create_tracer, reset_tracer


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


def _ok_resp(**kwargs: object) -> ExecuteResp:
    base: dict[str, object] = {
        "success": True,
        "result_rows": [{"product": "A", "rate": 0.2}],
        "result_columns": ["product", "rate"],
        "duration_ms": 5,
    }
    base.update(kwargs)
    return ExecuteResp.model_validate(base)


def _state(
    *,
    execute_resp: ExecuteResp,
    constraints: list[dict[str, Any]] | None = None,
    enable_reflector: bool = True,
    retry_count: int = 0,
    fingerprints: list[str] | None = None,
    llm_calls_used: int = 0,
    max_retry: int = 3,
    max_llm: int = 20,
    sub_id: str = "st-1",
) -> dict[str, Any]:
    state: dict[str, Any] = dict(
        create_initial_task_state(
            dataset_id="ds-1",
            user_query="退货率",
            task_id="t-rv",
            strategy_flags=StrategyFlags(enable_reflector=enable_reflector),
        )
    )
    state["execute_resp"] = execute_resp
    state["structured_constraints"] = constraints or []
    state["current_sub_task_id"] = sub_id
    state["retry_counts"] = {sub_id: retry_count} if retry_count else {}
    state["error_fingerprints"] = list(fingerprints or [])
    state["llm_calls_used"] = llm_calls_used
    state["_settings"] = Settings.model_validate(
        {"max_retry_rounds": max_retry, "max_llm_calls_per_task": max_llm}
    )
    return state


def test_valid_routes_to_report() -> None:
    state = _state(
        execute_resp=_ok_resp(),
        constraints=[
            {"type": "require_columns", "columns": ["product", "rate"]},
            {"type": "ratio_range", "column": "rate", "min": 0.0, "max": 1.0},
        ],
    )
    delta = NodeResultValidate(settings=state["_settings"]).run(state)  # type: ignore[arg-type]
    assert delta["result_validate"].valid is True
    assert delta["next_node"] == "report"


def test_ratio_out_of_range_healable_loops_generate_op() -> None:
    state = _state(
        execute_resp=_ok_resp(result_rows=[{"rate": 1.5}], result_columns=["rate"]),
        constraints=[{"type": "ratio_range", "column": "rate", "min": 0.0, "max": 1.0}],
    )
    delta = NodeResultValidate(settings=state["_settings"]).run(state)  # type: ignore[arg-type]
    assert delta["result_validate"].valid is False
    assert delta["result_validate"].anomaly_type == "ratio_out_of_range"
    assert delta["next_node"] == "generate_op"
    assert delta["sub_task_results"][0].status == "healing"
    assert delta["error_fingerprints"]
    assert delta["fix_hint"]


def test_empty_anomaly_marked() -> None:
    state = _state(
        execute_resp=_ok_resp(result_rows=[], result_columns=[]),
        constraints=[{"type": "non_empty"}],
    )
    delta = NodeResultValidate(settings=state["_settings"]).run(state)  # type: ignore[arg-type]
    assert delta["result_validate"].anomaly_type == "empty_anomaly"
    assert delta["next_node"] == "generate_op"


def test_unhealable_when_reflector_disabled_no_loop() -> None:
    state = _state(
        execute_resp=_ok_resp(result_rows=[{"rate": 2.0}], result_columns=["rate"]),
        constraints=[{"type": "ratio_range", "column": "rate", "min": 0.0, "max": 1.0}],
        enable_reflector=False,
    )
    delta = NodeResultValidate(settings=state["_settings"]).run(state)  # type: ignore[arg-type]
    assert delta["result_validate"].anomaly_type == "ratio_out_of_range"
    assert delta["next_node"] == "report"
    assert delta["sub_task_results"][0].status == "failed"


def test_unhealable_on_second_same_anomaly_fingerprint() -> None:
    """Second consecutive identical anomaly must not loop (mirror Reflect)."""
    state = _state(
        execute_resp=_ok_resp(result_rows=[{"rate": 1.5}], result_columns=["rate"]),
        constraints=[{"type": "ratio_range", "column": "rate", "min": 0.0, "max": 1.0}],
        retry_count=1,
    )
    # First pass appends fingerprint via node; simulate prior identical fp by
    # pre-seeding one matching fingerprint so after append we get [fp, fp].
    node = NodeResultValidate(settings=state["_settings"])
    first = node.run(state)  # type: ignore[arg-type]
    assert first["next_node"] == "generate_op"
    fp = first["error_fingerprints"][-1]

    state2 = _state(
        execute_resp=_ok_resp(result_rows=[{"rate": 1.5}], result_columns=["rate"]),
        constraints=[{"type": "ratio_range", "column": "rate", "min": 0.0, "max": 1.0}],
        retry_count=1,
        fingerprints=[fp],
    )
    second = node.run(state2)  # type: ignore[arg-type]
    assert second["next_node"] == "report"
    assert second["sub_task_results"][0].status == "failed"


def test_nl_constraint_ignored_still_valid() -> None:
    state = _state(
        execute_resp=_ok_resp(),
        constraints=[
            {"type": "nl_constraint", "text": "排除促销订单"},
            {"type": "require_columns", "columns": ["product"]},
        ],
    )
    delta = NodeResultValidate(settings=state["_settings"]).run(state)  # type: ignore[arg-type]
    assert delta["result_validate"].valid is True
    assert delta["next_node"] == "report"


def test_retry_limit_blocks_heal_loop() -> None:
    state = _state(
        execute_resp=_ok_resp(result_rows=[{"rate": 9.0}], result_columns=["rate"]),
        constraints=[{"type": "ratio_range", "column": "rate", "min": 0.0, "max": 1.0}],
        retry_count=3,
        max_retry=3,
    )
    delta = NodeResultValidate(settings=state["_settings"]).run(state)  # type: ignore[arg-type]
    assert delta["next_node"] == "report"
