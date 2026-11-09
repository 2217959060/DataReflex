# -*- coding: utf-8 -*-
"""Graph 节点 ``Node_FallbackReport``：模型不可用时的静态诊断报告（非分析结论）。

上游：Node_Report 判定需降级（``enable_fallback_report`` + model unavailable）。
下游业务：``modules/report/fallback``。
始终 ``next_node=end``；开关关闭则不生成降级内容。

输出须标明「非分析结论」，避免被当成正式 BI 答案。

See ``tasks.md`` T31 / D-FALLBACK-01。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, Protocol, cast

from data_reflex.graph_engine.nodes.base import BaseNode
from data_reflex.models.schemas import (
    FallbackReportReq,
    FallbackReportResp,
    IntentParseResp,
    ReflectResp,
    SubTaskResult,
    TableSchema,
)
from data_reflex.models.state import StrategyFlags, TaskState
from data_reflex.modules.audit.store import AuditStore
from data_reflex.modules.report.fallback import FallbackReportBuilder
from data_reflex.utils.logger import get_logger
from data_reflex.utils.tracing import Tracer

logger = get_logger(__name__)


class _FallbackBuilderLike(Protocol):
    def build(self, req: FallbackReportReq) -> FallbackReportResp: ...


class NodeFallbackReport(BaseNode):
    """在开关开启时写出 fallback_report，然后结束任务。"""

    name: ClassVar[str] = "Node_FallbackReport"

    def __init__(
        self,
        *,
        audit_store: AuditStore | None = None,
        tracer: Tracer | None = None,
        builder: _FallbackBuilderLike | None = None,
        max_attempts: int = 2,
    ) -> None:
        super().__init__(
            audit_store=audit_store, tracer=tracer, max_attempts=max_attempts
        )
        self._builder: _FallbackBuilderLike = builder or FallbackReportBuilder()

    def execute(self, state: TaskState) -> dict[str, Any]:
        flags = state.get("strategy_flags") or StrategyFlags()
        if not isinstance(flags, StrategyFlags):
            flags = StrategyFlags.model_validate(flags)

        # Acceptance: switch off → do not trigger fallback content.
        if not flags.enable_fallback_report:
            return {
                "fallback_report": None,
                "status": str(state.get("status") or "failed"),
                "next_node": "end",
            }

        results = list(state.get("sub_task_results") or [])
        completed = [r for r in results if r.status == "success"]
        failed_ids = [
            r.sub_task_id for r in results if r.status in {"failed", "terminated"}
        ]
        req = FallbackReportReq(
            task_id=str(state.get("task_id") or "-"),
            dataset_id=str(state.get("dataset_id") or "-"),
            failed_node=_failed_node(state),
            failed_sub_tasks=failed_ids,
            completed_results=completed,
            parsed_keywords=_parsed_keywords(state),
            table_schemas=_table_schemas(state),
        )

        try:
            report = self._builder.build(req)
        except Exception as exc:  # noqa: BLE001 — still finish the task
            logger.warning(
                "fallback builder failed; emitting minimal diagnostic",
                extra={
                    "task_id": req.task_id,
                    "dataset_id": req.dataset_id,
                    "error": type(exc).__name__,
                },
            )
            report = _minimal_fallback(req, results, exc)

        return {
            "fallback_report": report,
            "status": "fallback",
            "next_node": "end",
        }


def _failed_node(state: TaskState) -> str:
    bag = cast(Mapping[str, Any], state)
    explicit = bag.get("failed_node")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    reflect = bag.get("reflect")
    if isinstance(reflect, ReflectResp) and reflect.melt_reason == "llm_call_limit":
        return "Node_Reflect"
    if isinstance(reflect, Mapping) and reflect.get("melt_reason") == "llm_call_limit":
        return "Node_Reflect"
    if bag.get("model_unavailable") is True:
        return "model"
    return "Node_Report"


def _parsed_keywords(state: TaskState) -> list[str]:
    keywords: list[str] = []
    intent_raw = state.get("intent")
    if intent_raw is None:
        return keywords
    intent = (
        intent_raw
        if isinstance(intent_raw, IntentParseResp)
        else IntentParseResp.model_validate(intent_raw)
    )
    if intent.question_tag:
        keywords.append(f"question_tag={intent.question_tag}")
    keywords.extend(str(c) for c in intent.constraints)
    if intent.normalized_query:
        keywords.append(f"normalized={intent.normalized_query[:80]}")
    return keywords


def _table_schemas(state: TaskState) -> list[TableSchema]:
    bag = cast(Mapping[str, Any], state)
    raw = bag.get("table_schemas")
    if not isinstance(raw, list):
        return []
    out: list[TableSchema] = []
    for item in raw:
        if isinstance(item, TableSchema):
            out.append(item)
        elif isinstance(item, Mapping):
            out.append(TableSchema.model_validate(item))
    return out


def _minimal_fallback(
    req: FallbackReportReq,
    results: list[SubTaskResult],
    exc: BaseException,
) -> FallbackReportResp:
    return FallbackReportResp(
        summary=(
            f"【非分析结论】【静态诊断】 task={req.task_id} "
            f"builder_error={type(exc).__name__}; "
            "本报告仅为诊断信息，不是数据分析结论。"
        ),
        data_overview={"dataset_id": req.dataset_id, "table_count": 0},
        parse_status={"parsed_keywords": list(req.parsed_keywords)},
        failure_scope={
            "failed_node": req.failed_node,
            "failed_sub_tasks": list(req.failed_sub_tasks),
            "error": type(exc).__name__,
        },
        manual_suggestions=[
            "可在表格工具中按已知字段手工汇总；未生成可执行查询或脚本。",
            "恢复模型后重新提交问题以获得正式分析报告。",
        ],
        completed_results=[
            {
                "sub_task_id": r.sub_task_id,
                "status": r.status,
                "row_count": len(r.result_rows),
            }
            for r in results
            if r.status == "success"
        ],
        is_analysis_conclusion=False,
    )
