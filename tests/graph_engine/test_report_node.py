# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from data_reflex.graph_engine.nodes.report import NodeReport
from data_reflex.models.schemas import ReflectResp, ReportReq, ReportResp, SubTaskResult
from data_reflex.models.state import StrategyFlags, create_initial_task_state
from data_reflex.utils.tracing import create_tracer, reset_tracer


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


def _success(sub_id: str = "st-1") -> SubTaskResult:
    rows = [{"channel": "APP", "amount": 100}, {"channel": "Web", "amount": 80}]
    return SubTaskResult(
        sub_task_id=sub_id,
        status="success",
        result_rows=rows,
        result_columns=["channel", "amount"],
        duration_ms=10,
    )


def _failed(sub_id: str = "st-2", reason: str = "sandbox timeout") -> SubTaskResult:
    return SubTaskResult(
        sub_task_id=sub_id,
        status="failed",
        error_summary=reason,
        duration_ms=5,
    )


def _empty_success(sub_id: str = "empty") -> SubTaskResult:
    return SubTaskResult(
        sub_task_id=sub_id,
        status="success",
        result_rows=[],
        result_columns=["channel"],
        duration_ms=3,
    )


def _state(
    *,
    results: list[SubTaskResult],
    chart_requests: list[dict[str, Any]] | None = None,
    enable_fallback: bool = True,
    reflect: ReflectResp | None = None,
    model_unavailable: bool = False,
) -> dict[str, Any]:
    state: dict[str, Any] = dict(
        create_initial_task_state(
            dataset_id="ds-1",
            user_query="渠道对比",
            task_id="t-report",
            strategy_flags=StrategyFlags(enable_fallback_report=enable_fallback),
        )
    )
    state["sub_task_results"] = results
    if chart_requests is not None:
        state["chart_requests"] = chart_requests
    if reflect is not None:
        state["reflect"] = reflect
    if model_unavailable:
        state["model_unavailable"] = True
    return state


def test_partial_success_includes_failure_and_completed() -> None:
    node = NodeReport()
    delta = node.run(
        _state(results=[_success("ok-1"), _failed("bad-1", "sandbox timeout")])  # type: ignore[arg-type]
    )
    report = delta["final_report"]
    assert report.partial_success is True
    assert "sandbox timeout" in report.summary
    assert len(report.tables) == 1
    assert report.tables[0]["sub_task_id"] == "ok-1"
    assert report.source_map["bad-1"]["status"] == "failed"
    assert delta["next_node"] == "end"
    assert delta["status"] == "success"


def test_empty_data_chart_not_fabricated() -> None:
    node = NodeReport()
    delta = node.run(
        _state(  # type: ignore[arg-type]
            results=[_empty_success()],
            chart_requests=[
                {"type": "bar", "sub_task_id": "empty", "title": "空图"},
            ],
        )
    )
    charts = delta["final_report"].charts
    assert len(charts) == 1
    assert charts[0].series == []
    assert charts[0].alternative_note
    assert (
        "not fabricated" in charts[0].alternative_note.lower()
        or "empty" in (charts[0].alternative_note or "").lower()
    )


def test_unsupported_chart_gets_alternative() -> None:
    node = NodeReport()
    delta = node.run(
        _state(  # type: ignore[arg-type]
            results=[_success("c1")],
            chart_requests=[{"type": "pie", "sub_task_id": "c1", "title": "份额"}],
        )
    )
    chart = delta["final_report"].charts[0]
    assert chart.chart_type == "table"
    assert chart.alternative_note
    assert "pie" in chart.alternative_note


def test_builder_exception_emits_minimal_report() -> None:
    class Boom:
        def build(
            self,
            req: ReportReq,
            *,
            chart_requests: Sequence[Mapping[str, Any]] | None = None,
        ) -> ReportResp:
            raise RuntimeError("template boom")

    node = NodeReport(builder=Boom())
    delta = node.run(
        _state(results=[_success("ok"), _failed("bad", "err")])  # type: ignore[arg-type]
    )
    report = delta["final_report"]
    assert "minimal report" in report.summary
    assert "builder error" in report.summary
    assert "err" in report.summary
    assert report.tables  # completed success rows preserved
    assert delta["next_node"] == "end"


def test_llm_call_limit_routes_to_fallback_when_enabled() -> None:
    node = NodeReport()
    reflect = ReflectResp(
        should_retry=False,
        circuit_breaker=True,
        melt_reason="llm_call_limit",
        final_status="melted",
    )
    delta = node.run(
        _state(results=[_failed("x", "exhausted")], reflect=reflect)  # type: ignore[arg-type]
    )
    assert delta["next_node"] == "fallback_report"
    assert delta["status"] == "fallback"


def test_fallback_disabled_ends_even_if_model_unavailable() -> None:
    node = NodeReport()
    delta = node.run(
        _state(  # type: ignore[arg-type]
            results=[_failed("x")],
            enable_fallback=False,
            model_unavailable=True,
        )
    )
    assert delta["next_node"] == "end"
