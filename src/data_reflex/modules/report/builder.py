# -*- coding: utf-8 -*-
"""正式报告组装：表格、图表、部分成功、中文结论草稿。

上游：``Node_Report``。
本文件：汇总子任务结果；跳过 ``report=False`` 中间表（V2-3）；调用 conclusion。
下游：``ReportResp`` 进状态；模型不可用时节点可能再走 Fallback。

See ``tasks.md`` T16 / D-REPORT-01。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from data_reflex.config.settings import Settings
from data_reflex.models.schemas import (
    ChartSpec,
    PlanResp,
    ReportReq,
    ReportResp,
    SubTaskResult,
)
from data_reflex.modules.report.conclusion import build_conclusion
from data_reflex.utils.logger import get_logger

logger = get_logger(__name__)

SupportedChart = Literal["bar", "line", "table"]
_SUPPORTED: frozenset[str] = frozenset({"bar", "line", "table"})
_SUCCESS_STATUSES = frozenset({"success"})
_FAILED_STATUSES = frozenset({"failed", "terminated"})


class ReportBuilder:
    """从子任务结果拼 ReportResp；可选图表请求与结论润色入口。"""

    def build(
        self,
        req: ReportReq,
        *,
        chart_requests: Sequence[Mapping[str, Any]] | None = None,
        plan: PlanResp | Mapping[str, Any] | None = None,
        user_query: str | None = None,
        question_tag: str | None = None,
        settings: Settings | None = None,
    ) -> ReportResp:
        """Aggregate results.

        ``chart_requests`` / ``plan`` / conclusion fields are intentionally
        outside ``ReportReq`` (IO frozen). Plan ``expected_output.report=False``
        hides intermediate tables (V2-3). Conclusion uses templates (+ optional
        LLM polish when ``enable_llm_ops``) per V2-4.
        """
        results = list(req.sub_task_results)
        successes = [r for r in results if r.status in _SUCCESS_STATUSES]
        failures_from_results = [r for r in results if r.status in _FAILED_STATUSES]
        failure_notes = self._collect_failure_notes(req.failures, failures_from_results)

        partial = bool(successes) and (
            bool(failures_from_results) or bool(req.failures)
        )
        all_failed = not successes and (
            bool(failures_from_results) or bool(req.failures)
        )
        all_ok = bool(successes) and not failures_from_results and not req.failures

        reportable = _reportable_successes(successes, plan)
        machine = self._build_summary(
            task_id=req.task_id,
            successes=successes,
            failure_notes=failure_notes,
            partial=partial,
            all_failed=all_failed,
            all_ok=all_ok,
        )
        conclusion = None
        if reportable and not all_failed:
            conclusion = build_conclusion(
                user_query=user_query or "",
                question_tag=question_tag,
                reportable=reportable,
                settings=settings,
            )
        summary = self._compose_summary(
            conclusion=conclusion,
            machine=machine,
            failure_notes=failure_notes,
            partial=partial,
            all_failed=all_failed,
        )
        tables = self._build_tables(reportable)
        requests = list(chart_requests or [])
        if not requests:
            requests = _chart_requests_from_plan(plan, reportable)
        charts = self._build_charts(reportable, requests)
        source_map = self._build_source_map(results)

        logger.info(
            "report built",
            extra={
                "task_id": req.task_id,
                "dataset_id": "-",
                "partial_success": partial,
                "table_count": len(tables),
                "chart_count": len(charts),
            },
        )
        return ReportResp(
            summary=summary,
            tables=tables,
            charts=charts,
            source_map=source_map,
            partial_success=partial,
        )

    def _compose_summary(
        self,
        *,
        conclusion: str | None,
        machine: str,
        failure_notes: list[str],
        partial: bool,
        all_failed: bool,
    ) -> str:
        if conclusion:
            parts = [conclusion]
            if partial and failure_notes:
                parts.append("部分失败：" + "；".join(failure_notes) + "。")
            # Keep machine line for audit/tests that grep task=/successfully.
            parts.append(machine)
            return "\n".join(parts)
        if all_failed:
            return machine
        return machine

    def _collect_failure_notes(
        self,
        failures: list[dict[str, Any]],
        failed_results: list[SubTaskResult],
    ) -> list[str]:
        notes: list[str] = []
        for item in failures:
            sub = str(item.get("sub_task_id") or item.get("node") or "unknown")
            reason = str(
                item.get("reason")
                or item.get("error")
                or item.get("detail")
                or "failed"
            )
            notes.append(f"{sub}: {reason}")
        for res in failed_results:
            if res.error_summary:
                note = f"{res.sub_task_id}: {res.error_summary}"
                if note not in notes:
                    notes.append(note)
            elif not any(res.sub_task_id in n for n in notes):
                notes.append(f"{res.sub_task_id}: status={res.status}")
        return notes

    def _build_summary(
        self,
        *,
        task_id: str,
        successes: list[SubTaskResult],
        failure_notes: list[str],
        partial: bool,
        all_failed: bool,
        all_ok: bool,
    ) -> str:
        parts = [f"task={task_id}"]
        if all_ok:
            parts.append(f"completed successfully with {len(successes)} sub-task(s).")
        elif partial:
            parts.append(
                f"partial success: {len(successes)} succeeded; "
                f"{len(failure_notes)} failure(s)."
            )
            if failure_notes:
                parts.append("Failures: " + "; ".join(failure_notes) + ".")
        elif all_failed:
            parts.append("no successful sub-tasks.")
            if failure_notes:
                parts.append("Failures: " + "; ".join(failure_notes) + ".")
        else:
            parts.append(
                f"status interim: {len(successes)} success result(s) recorded."
            )
        return " ".join(parts)

    def _build_tables(self, successes: list[SubTaskResult]) -> list[dict[str, Any]]:
        tables: list[dict[str, Any]] = []
        for res in successes:
            tables.append(
                {
                    "sub_task_id": res.sub_task_id,
                    "columns": list(res.result_columns)
                    or (list(res.result_rows[0].keys()) if res.result_rows else []),
                    "rows": list(res.result_rows),
                    "row_count": len(res.result_rows),
                }
            )
        return tables

    def _build_source_map(self, results: list[SubTaskResult]) -> dict[str, Any]:
        mapping: dict[str, Any] = {}
        for res in results:
            mapping[res.sub_task_id] = {
                "status": res.status,
                "row_count": len(res.result_rows),
                "columns": list(res.result_columns),
                "error_summary": res.error_summary,
            }
        return mapping

    def _build_charts(
        self,
        successes: list[SubTaskResult],
        chart_requests: Sequence[Mapping[str, Any]],
    ) -> list[ChartSpec]:
        if not chart_requests:
            # Auto: one table chart per successful non-empty result.
            charts: list[ChartSpec] = []
            for res in successes:
                charts.append(self._chart_for_result(res, preferred="table", title=""))
            return charts

        charts = []
        by_id = {r.sub_task_id: r for r in successes}
        for req in chart_requests:
            raw_type = str(req.get("type") or req.get("chart_type") or "table").lower()
            title = str(req.get("title") or "")
            sub_id = str(req.get("sub_task_id") or "")
            result = (
                by_id.get(sub_id) if sub_id else (successes[0] if successes else None)
            )

            if raw_type not in _SUPPORTED:
                alt = (
                    f"chart type {raw_type!r} is not supported in v1; "
                    "substituted with table. Supported: bar, line, table."
                )
                if result is None or not result.result_rows:
                    charts.append(
                        ChartSpec(
                            chart_type="table",
                            title=title or "unsupported chart",
                            alternative_note=alt
                            + (
                                " No data available to render."
                                if result is None or not result.result_rows
                                else ""
                            ),
                            series=[],
                        )
                    )
                else:
                    chart = self._chart_for_result(
                        result, preferred="table", title=title
                    )
                    chart = chart.model_copy(update={"alternative_note": alt})
                    charts.append(chart)
                continue

            preferred: SupportedChart = raw_type  # type: ignore[assignment]
            if result is None:
                charts.append(
                    ChartSpec(
                        chart_type="table",
                        title=title or "missing data",
                        alternative_note="no matching successful sub-task for chart",
                        series=[],
                    )
                )
                continue
            charts.append(
                self._chart_for_result(
                    result,
                    preferred=preferred,
                    title=title,
                    x_field=req.get("x_field"),
                    y_field=req.get("y_field"),
                )
            )
        return charts

    def _chart_for_result(
        self,
        result: SubTaskResult,
        *,
        preferred: SupportedChart,
        title: str,
        x_field: object | None = None,
        y_field: object | None = None,
    ) -> ChartSpec:
        if not result.result_rows:
            return ChartSpec(
                chart_type="table",
                title=title or result.sub_task_id,
                alternative_note=(
                    f"sub-task {result.sub_task_id}: empty data; "
                    "chart skipped (not fabricated)."
                ),
                series=[],
            )

        columns = list(result.result_columns) or list(result.result_rows[0].keys())
        x = str(x_field) if x_field else (columns[0] if columns else None)
        y = None
        if y_field:
            y = str(y_field)
        elif len(columns) >= 2:
            y = columns[1]

        if preferred in {"bar", "line"} and (x is None or y is None):
            return ChartSpec(
                chart_type="table",
                title=title or result.sub_task_id,
                alternative_note=(
                    f"cannot draw {preferred} without x/y fields; "
                    "substituted with table."
                ),
                series=[{"rows": result.result_rows, "columns": columns}],
                x_field=x,
                y_field=y,
            )

        series: list[dict[str, Any]]
        if preferred == "table":
            series = [{"rows": result.result_rows, "columns": columns}]
        else:
            # Narrowed above: bar/line only proceed when both axes exist.
            x_key = x if x is not None else ""
            y_key = y if y is not None else ""
            series = [
                {
                    "x": [row.get(x_key) for row in result.result_rows],
                    "y": [row.get(y_key) for row in result.result_rows],
                }
            ]

        return ChartSpec(
            chart_type=preferred,
            title=title or result.sub_task_id,
            x_field=x,
            y_field=y,
            series=series,
            alternative_note=None,
        )


def _coerce_plan(
    plan: PlanResp | Mapping[str, Any] | None,
) -> PlanResp | None:
    if plan is None:
        return None
    if isinstance(plan, PlanResp):
        return plan
    try:
        return PlanResp.model_validate(plan)
    except Exception:  # noqa: BLE001
        return None


def _reportable_successes(
    successes: list[SubTaskResult],
    plan: PlanResp | Mapping[str, Any] | None,
) -> list[SubTaskResult]:
    """Drop intermediate steps marked ``expected_output.report=False``."""
    parsed = _coerce_plan(plan)
    if parsed is None or not parsed.sub_tasks:
        return successes
    flags = {
        st.task_id: st.expected_output.get("report", True) for st in parsed.sub_tasks
    }
    if not any(v is False for v in flags.values()):
        return successes
    return [r for r in successes if flags.get(r.sub_task_id, True) is not False]


def _chart_requests_from_plan(
    plan: PlanResp | Mapping[str, Any] | None,
    reportable: list[SubTaskResult],
) -> list[dict[str, Any]]:
    parsed = _coerce_plan(plan)
    if parsed is None:
        return []
    by_id = {st.task_id: st for st in parsed.sub_tasks}
    requests: list[dict[str, Any]] = []
    for res in reportable:
        st = by_id.get(res.sub_task_id)
        if st is None:
            continue
        chart = str(st.expected_output.get("chart") or "").lower()
        if chart not in _SUPPORTED:
            continue
        cols = list(res.result_columns) or (
            list(res.result_rows[0].keys()) if res.result_rows else []
        )
        x_field = cols[0] if cols else None
        y_field = None
        for cand in (
            "return_rate",
            "amount_sum",
            "amount",
            "order_count",
            "repurchase_rate",
        ):
            if cand in cols:
                y_field = cand
                break
        if y_field is None and len(cols) >= 2:
            y_field = cols[1]
        requests.append(
            {
                "type": chart,
                "title": res.sub_task_id,
                "sub_task_id": res.sub_task_id,
                "x_field": x_field,
                "y_field": y_field,
            }
        )
    return requests
