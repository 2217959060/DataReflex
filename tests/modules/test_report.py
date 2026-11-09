# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from data_reflex.models.schemas import ReportReq, SubTaskResult
from data_reflex.modules.report.builder import ReportBuilder


@pytest.fixture()
def builder() -> ReportBuilder:
    return ReportBuilder()


def _success(
    sub_id: str = "st-1", rows: list[dict[str, object]] | None = None
) -> SubTaskResult:
    data = (
        rows
        if rows is not None
        else [
            {"channel": "APP", "amount": 100},
            {"channel": "官网", "amount": 80},
        ]
    )
    return SubTaskResult(
        sub_task_id=sub_id,
        status="success",
        result_rows=data,
        result_columns=list(data[0].keys()) if data else [],
        duration_ms=10,
    )


def _failed(sub_id: str = "st-2", reason: str = "timeout") -> SubTaskResult:
    return SubTaskResult(
        sub_task_id=sub_id,
        status="failed",
        error_summary=reason,
        duration_ms=5,
    )


def test_full_success_report(builder: ReportBuilder) -> None:
    req = ReportReq(task_id="t1", sub_task_results=[_success()], failures=[])
    resp = builder.build(req)
    assert resp.partial_success is False
    assert len(resp.tables) == 1
    assert resp.tables[0]["row_count"] == 2
    assert "successfully" in resp.summary
    assert "st-1" in resp.source_map


def test_summary_includes_chinese_conclusion(builder: ReportBuilder) -> None:
    rows = [
        {"channel": "APP", "amount_sum": 1200},
        {"channel": "官网", "amount_sum": 800},
    ]
    req = ReportReq(
        task_id="t-concl",
        sub_task_results=[
            SubTaskResult(
                sub_task_id="st-compare-channel",
                status="success",
                result_rows=rows,
                result_columns=list(rows[0].keys()),
                duration_ms=1,
            )
        ],
        failures=[],
    )
    resp = builder.build(
        req,
        user_query="对比 APP 和官网销售额",
        question_tag="channel_compare",
    )
    assert "APP" in resp.summary
    assert "1200" in resp.summary
    assert "successfully" in resp.summary


def test_partial_success_includes_failure_in_summary(builder: ReportBuilder) -> None:
    req = ReportReq(
        task_id="t2",
        sub_task_results=[_success("ok-1"), _failed("bad-1", "sandbox timeout")],
        failures=[{"sub_task_id": "bad-1", "reason": "sandbox timeout"}],
    )
    resp = builder.build(req)
    assert resp.partial_success is True
    assert "partial success" in resp.summary.lower()
    assert "sandbox timeout" in resp.summary
    assert len(resp.tables) == 1
    assert resp.source_map["bad-1"]["status"] == "failed"


def test_unsupported_chart_type_gets_alternative_note(builder: ReportBuilder) -> None:
    req = ReportReq(task_id="t3", sub_task_results=[_success("c1")])
    resp = builder.build(
        req,
        chart_requests=[
            {
                "type": "pie",
                "title": "份额",
                "sub_task_id": "c1",
                "x_field": "channel",
                "y_field": "amount",
            }
        ],
    )
    assert len(resp.charts) == 1
    chart = resp.charts[0]
    assert chart.chart_type == "table"
    assert chart.alternative_note is not None
    assert "pie" in chart.alternative_note
    assert "not supported" in chart.alternative_note.lower()


def test_empty_data_skips_chart_without_fabricating(builder: ReportBuilder) -> None:
    empty = _success("empty", rows=[])
    req = ReportReq(task_id="t4", sub_task_results=[empty])
    resp = builder.build(
        req,
        chart_requests=[{"type": "bar", "sub_task_id": "empty", "title": "空图"}],
    )
    assert len(resp.charts) == 1
    chart = resp.charts[0]
    assert chart.series == []
    assert chart.alternative_note is not None
    assert "empty" in chart.alternative_note.lower()
    assert (
        "skipped" in chart.alternative_note.lower()
        or "fabricated" in chart.alternative_note.lower()
    )


def test_supported_bar_chart_builds_series(builder: ReportBuilder) -> None:
    req = ReportReq(task_id="t5", sub_task_results=[_success("b1")])
    resp = builder.build(
        req,
        chart_requests=[
            {
                "type": "bar",
                "sub_task_id": "b1",
                "x_field": "channel",
                "y_field": "amount",
                "title": "渠道对比",
            }
        ],
    )
    chart = resp.charts[0]
    assert chart.chart_type == "bar"
    assert chart.alternative_note is None
    assert chart.series and "x" in chart.series[0]
    assert chart.series[0]["x"] == ["APP", "官网"]


def test_all_failed_not_partial(builder: ReportBuilder) -> None:
    req = ReportReq(
        task_id="t6",
        sub_task_results=[_failed("f1"), _failed("f2")],
        failures=[{"sub_task_id": "f1", "reason": "syntax"}],
    )
    resp = builder.build(req)
    assert resp.partial_success is False
    assert resp.tables == []
    assert "syntax" in resp.summary or "f1" in resp.summary


def test_report_hides_intermediate_tables(builder: ReportBuilder) -> None:
    from data_reflex.models.schemas import PlanResp, SubTask

    plan = PlanResp(
        success=True,
        sub_tasks=[
            SubTask(
                task_id="st-agg-channel",
                description="agg",
                expected_output={
                    "columns": ["channel", "amount_sum"],
                    "report": False,
                },
            ),
            SubTask(
                task_id="st-compare-channel",
                description="compare",
                depends_on=["st-agg-channel"],
                expected_input={"reuse_result": True, "from_sub_task": "st-agg-channel"},
                expected_output={
                    "columns": ["channel", "amount_sum"],
                    "chart": "bar",
                    "report": True,
                },
            ),
        ],
    )
    rows = [
        {"channel": "APP", "amount_sum": 10},
        {"channel": "官网", "amount_sum": 8},
    ]
    req = ReportReq(
        task_id="t-dedup",
        sub_task_results=[
            _success("st-agg-channel", rows=rows),
            _success("st-compare-channel", rows=rows),
        ],
    )
    resp = builder.build(req, plan=plan)
    assert len(resp.tables) == 1
    assert resp.tables[0]["sub_task_id"] == "st-compare-channel"
    assert resp.charts
    assert resp.charts[0].chart_type == "bar"


def test_report_hides_intermediate_tables(builder: ReportBuilder) -> None:
    from data_reflex.models.schemas import PlanResp, SubTask

    plan = PlanResp(
        success=True,
        sub_tasks=[
            SubTask(
                task_id="st-agg-channel",
                description="agg",
                expected_output={"report": False},
            ),
            SubTask(
                task_id="st-compare-channel",
                description="compare",
                depends_on=["st-agg-channel"],
                expected_output={"report": True, "chart": "bar"},
            ),
        ],
    )
    rows = [{"channel": "APP", "amount_sum": 10, "order_count": 1}]
    req = ReportReq(
        task_id="t-dedup",
        sub_task_results=[
            _success("st-agg-channel", rows=rows),
            _success("st-compare-channel", rows=rows),
        ],
    )
    resp = builder.build(req, plan=plan)
    assert len(resp.tables) == 1
    assert resp.tables[0]["sub_task_id"] == "st-compare-channel"
    assert len(resp.charts) == 1
    assert resp.charts[0].chart_type == "bar"
