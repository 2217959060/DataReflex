# -*- coding: utf-8 -*-
"""Graph 节点 ``Node_Report``：汇总子任务结果，生成正式报告。

上游：成功收束、澄清/拒绝、安全拦截、自愈熔断等多条边。
下游业务：``modules/report/builder``（表格/图表/中文结论草稿）。
``next_node``：模型不可用且开了降级开关 → ``fallback_report``；否则 ``end``。

意图澄清文案会写入 summary（【需要澄清】），与人审确认不是同一条路径。

See ``tasks.md`` T30 / D-REPORT-01。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Protocol, cast

from data_reflex.graph_engine.nodes.base import BaseNode, truncate_error_text
from data_reflex.models.schemas import (
    ChartSpec,
    IntentParseResp,
    ReflectResp,
    ReportReq,
    ReportResp,
    SubTaskResult,
)
from data_reflex.models.state import StrategyFlags, TaskState
from data_reflex.modules.audit.store import AuditStore
from data_reflex.modules.report.builder import ReportBuilder
from data_reflex.utils.logger import get_logger
from data_reflex.utils.tracing import Tracer

logger = get_logger(__name__)


class _BuilderLike(Protocol):
    def build(
        self,
        req: ReportReq,
        *,
        chart_requests: Sequence[Mapping[str, Any]] | None = None,
        plan: Any = None,
        user_query: str | None = None,
        question_tag: str | None = None,
        settings: Any = None,
    ) -> ReportResp: ...


class NodeReport(BaseNode):
    """写 ``final_report``；必要时路由 fallback_report，否则结束。"""

    name: ClassVar[str] = "Node_Report"

    def __init__(
        self,
        *,
        audit_store: AuditStore | None = None,
        tracer: Tracer | None = None,
        builder: _BuilderLike | None = None,
        max_attempts: int = 2,
    ) -> None:
        super().__init__(
            audit_store=audit_store, tracer=tracer, max_attempts=max_attempts
        )
        self._builder: _BuilderLike = builder or ReportBuilder()

    def execute(self, state: TaskState) -> dict[str, Any]:
        task_id = str(state.get("task_id") or "-")
        results = list(state.get("sub_task_results") or [])
        failures = _failures_from_state(state, results)
        clarify_note = _clarification_note(state)
        if clarify_note:
            # Surface intent clarification in the report (not the same as human confirm).
            failures = [
                {
                    "sub_task_id": "intent_parse",
                    "reason": clarify_note,
                },
                *failures,
            ]
        chart_requests = _chart_requests(state)
        user_query, question_tag = _query_context(state)

        req = ReportReq(
            task_id=task_id,
            sub_task_results=results,
            failures=failures,
        )
        try:
            report = self._builder.build(
                req,
                chart_requests=chart_requests or None,
                plan=state.get("plan"),
                user_query=user_query,
                question_tag=question_tag,
            )
        except Exception as exc:  # noqa: BLE001 — must still finish the task
            logger.warning(
                "report builder failed; emitting minimal summary",
                extra={
                    "task_id": task_id,
                    "dataset_id": str(state.get("dataset_id") or "-"),
                    "error": type(exc).__name__,
                },
            )
            report = _minimal_report(task_id, results, failures, exc)

        if clarify_note and "【需要澄清】" not in report.summary:
            report = report.model_copy(
                update={
                    "summary": "【需要澄清】" + clarify_note + "\n\n" + report.summary
                }
            )

        flags = state.get("strategy_flags") or StrategyFlags()
        if not isinstance(flags, StrategyFlags):
            flags = StrategyFlags.model_validate(flags)

        if flags.enable_fallback_report and _model_unavailable(state):
            next_node = "fallback_report"
            status = "fallback"
        else:
            next_node = "end"
            status = _terminal_status(results, report)

        return {
            "final_report": report,
            "status": status,
            "next_node": next_node,
        }


def _failures_from_state(
    state: TaskState, results: list[SubTaskResult]
) -> list[dict[str, Any]]:
    bag = cast(Mapping[str, Any], state)
    raw = bag.get("failures")
    failures: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping):
                failures.append(dict(item))

    # Also surface failed sub-tasks so summary always has attribution.
    seen = {str(f.get("sub_task_id") or "") for f in failures}
    for res in results:
        if res.status in {"failed", "terminated"} and res.sub_task_id not in seen:
            failures.append(
                {
                    "sub_task_id": res.sub_task_id,
                    "reason": res.error_summary or f"status={res.status}",
                }
            )
    return failures


def _chart_requests(state: TaskState) -> list[dict[str, Any]]:
    bag = cast(Mapping[str, Any], state)
    raw = bag.get("chart_requests")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping):
            out.append(dict(item))
    return out


def _query_context(state: TaskState) -> tuple[str, str | None]:
    """Return ``(user_query, question_tag)`` for conclusion drafting."""
    bag = cast(Mapping[str, Any], state)
    user_query = str(bag.get("user_query") or "").strip()
    raw = state.get("intent")
    if raw is None:
        return user_query, None
    intent = (
        raw if isinstance(raw, IntentParseResp) else IntentParseResp.model_validate(raw)
    )
    tag = getattr(intent, "question_tag", None)
    return user_query, str(tag) if tag else None


def _clarification_note(state: TaskState) -> str | None:
    """Pull clarification / reject text from intent for the user-facing report."""
    raw = state.get("intent")
    if raw is None:
        return None
    intent = (
        raw if isinstance(raw, IntentParseResp) else IntentParseResp.model_validate(raw)
    )
    if intent.need_clarification and intent.clarification_questions:
        return " ".join(str(q) for q in intent.clarification_questions if q)
    if not intent.in_scope and intent.reject_reason:
        return str(intent.reject_reason)
    return None


def _model_unavailable(state: TaskState) -> bool:
    """True when LLM budget is exhausted or an explicit unavailable flag is set."""
    bag = cast(Mapping[str, Any], state)
    if bag.get("model_unavailable") is True:
        return True
    reflect = bag.get("reflect")
    if isinstance(reflect, ReflectResp):
        return reflect.melt_reason == "llm_call_limit"
    if isinstance(reflect, Mapping):
        return reflect.get("melt_reason") == "llm_call_limit"
    return False


def _terminal_status(results: list[SubTaskResult], report: ReportResp) -> str:
    if report.partial_success:
        return "success"
    if any(r.status == "success" for r in results):
        return "success"
    if any(r.status == "terminated" for r in results):
        return "terminated"
    if results and all(r.status in {"failed", "terminated"} for r in results):
        return "failed"
    if not results:
        return "failed"
    return "success"


def _minimal_report(
    task_id: str,
    results: list[SubTaskResult],
    failures: list[dict[str, Any]],
    exc: BaseException,
) -> ReportResp:
    """Guaranteed structured summary when the builder raises."""
    notes = [
        f"{f.get('sub_task_id', '?')}: {f.get('reason', 'failed')}" for f in failures
    ]
    for res in results:
        if res.status in {"failed", "terminated"} and res.error_summary:
            note = f"{res.sub_task_id}: {res.error_summary}"
            if note not in notes:
                notes.append(note)
    err = truncate_error_text(f"{type(exc).__name__}: {exc}")
    summary = f"task={task_id} minimal report (builder error: {err}). " + (
        "Failures: " + "; ".join(notes) + "." if notes else "No failure notes."
    )
    source_map = {
        r.sub_task_id: {
            "status": r.status,
            "row_count": len(r.result_rows),
            "error_summary": r.error_summary,
        }
        for r in results
    }
    tables = [
        {
            "sub_task_id": r.sub_task_id,
            "columns": list(r.result_columns),
            "rows": list(r.result_rows),
            "row_count": len(r.result_rows),
        }
        for r in results
        if r.status == "success" and r.result_rows
    ]
    return ReportResp(
        summary=summary,
        tables=tables,
        charts=[
            ChartSpec(
                chart_type="table",
                title="minimal",
                alternative_note="builder failed; charts omitted",
                series=[],
            )
        ],
        source_map=source_map,
        partial_success=bool(tables) and bool(notes),
    )
