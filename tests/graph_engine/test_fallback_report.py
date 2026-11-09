# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

import pytest

from data_reflex.graph_engine.nodes.fallback_report import NodeFallbackReport
from data_reflex.models.schemas import (
    ColumnSchema,
    IntentParseResp,
    ReflectResp,
    SubTaskResult,
    TableSchema,
)
from data_reflex.models.state import StrategyFlags, create_initial_task_state
from data_reflex.modules.report.fallback import FallbackReportBuilder
from data_reflex.utils.tracing import create_tracer, reset_tracer


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


def _schema() -> TableSchema:
    return TableSchema(
        file_name="orders.csv",
        row_count=100,
        columns=[
            ColumnSchema(name="channel", dtype="string", null_ratio=0.0),
            ColumnSchema(name="amount", dtype="float", null_ratio=0.0),
            ColumnSchema(name="phone", dtype="string", null_ratio=0.5),
        ],
    )


def _state(
    *,
    enable_fallback: bool = True,
    model_unavailable: bool = True,
    with_schema: bool = True,
) -> dict[str, Any]:
    state: dict[str, Any] = dict(
        create_initial_task_state(
            dataset_id="ds-1",
            user_query="对比渠道",
            task_id="t-fb",
            strategy_flags=StrategyFlags(enable_fallback_report=enable_fallback),
        )
    )
    state["intent"] = IntentParseResp(
        in_scope=True,
        question_tag="channel_compare",
        constraints=["channel IN ('APP','官网')"],
        normalized_query="tag=channel_compare",
    )
    state["sub_task_results"] = [
        SubTaskResult(
            sub_task_id="ok-1",
            status="success",
            result_rows=[{"channel": "APP", "amount": 1}],
            result_columns=["channel", "amount"],
        ),
        SubTaskResult(
            sub_task_id="bad-1",
            status="failed",
            error_summary="llm exhausted",
        ),
    ]
    state["reflect"] = ReflectResp(
        should_retry=False,
        circuit_breaker=True,
        melt_reason="llm_call_limit",
        final_status="melted",
    )
    if model_unavailable:
        state["model_unavailable"] = True
    if with_schema:
        state["table_schemas"] = [_schema()]
    return state


def test_fallback_marks_non_analysis_and_static_diagnostic() -> None:
    node = NodeFallbackReport()
    delta = node.run(_state())  # type: ignore[arg-type]
    report = delta["fallback_report"]
    assert report is not None
    assert report.is_analysis_conclusion is False
    assert "静态诊断" in report.summary
    assert "非分析结论" in report.summary
    assert delta["next_node"] == "end"
    assert delta["status"] == "fallback"
    assert report.failure_scope["failed_node"] == "Node_Reflect"
    assert "ok-1" in {c["sub_task_id"] for c in report.completed_results}
    assert report.data_overview["table_count"] == 1
    assert report.manual_suggestions


def test_no_fabricated_analysis_or_scripts() -> None:
    node = NodeFallbackReport()
    delta = node.run(_state())  # type: ignore[arg-type]
    report = delta["fallback_report"]
    blob = report.summary + "\n" + "\n".join(report.manual_suggestions)
    for bad in ("SELECT ", "import ", "分析结论：", "因此可以判定"):
        assert bad not in blob
    # Sensitive column should not be suggested as analysis field.
    assert "phone" not in "\n".join(report.manual_suggestions)


def test_switch_off_does_not_trigger() -> None:
    node = NodeFallbackReport()
    delta = node.run(_state(enable_fallback=False))  # type: ignore[arg-type]
    assert delta["fallback_report"] is None
    assert delta["next_node"] == "end"


def test_builder_unit_is_analysis_false() -> None:
    from data_reflex.models.schemas import FallbackReportReq

    resp = FallbackReportBuilder().build(
        FallbackReportReq(
            task_id="t",
            dataset_id="d",
            failed_node="model",
            failed_sub_tasks=["a"],
            completed_results=[],
            parsed_keywords=["channel"],
            table_schemas=[_schema()],
        )
    )
    assert resp.is_analysis_conclusion is False
    assert "静态诊断" in resp.summary
