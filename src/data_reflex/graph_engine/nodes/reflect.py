# -*- coding: utf-8 -*-
"""Graph 节点 ``Node_Reflect``：把 Reflector 的决策写回状态，并告诉图下一步去哪。

读代码时抓住两件事即可：
1. 调 ``Reflector.reflect()`` 得到 should_retry / fix_hint
2. 写 ``next_node``：
   - 重试 → ``generate_op``（并把 fix_hint 放进状态）
   - 熔断/失败 → ``report``（结束自愈，出报告）

这是「任务内自愈」，不是前端澄清提问。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, Literal, Protocol, cast

from data_reflex.config.settings import Settings, get_settings
from data_reflex.graph_engine.nodes.base import BaseNode
from data_reflex.models.schemas import (
    ErrorClassifyResp,
    PlanResp,
    ReflectReq,
    ReflectResp,
    SubTask,
    SubTaskResult,
)
from data_reflex.models.state import StrategyFlags, TaskState
from data_reflex.modules.audit.store import AuditStore
from data_reflex.modules.reflect.reflector import Reflector
from data_reflex.utils.tracing import Tracer


class _ReflectorLike(Protocol):
    def reflect(self, req: ReflectReq) -> ReflectResp: ...


class NodeReflect(BaseNode):
    """Decide heal retry vs melt; route ``generate_op`` or ``report``."""

    name: ClassVar[str] = "Node_Reflect"

    def __init__(
        self,
        *,
        audit_store: AuditStore | None = None,
        tracer: Tracer | None = None,
        reflector: _ReflectorLike | None = None,
        settings: Settings | None = None,
        max_attempts: int = 2,
    ) -> None:
        super().__init__(
            audit_store=audit_store, tracer=tracer, max_attempts=max_attempts
        )
        self._settings = settings if settings is not None else get_settings()
        self._reflector: _ReflectorLike = reflector or Reflector()

    def execute(self, state: TaskState) -> dict[str, Any]:
        """从状态拼 ReflectReq → 决策 → 返回状态增量（含 next_node）。"""
        flags = state.get("strategy_flags") or StrategyFlags()
        if not isinstance(flags, StrategyFlags):
            flags = StrategyFlags.model_validate(flags)

        # 上一节点 ErrorClassify 的结果（类型/指纹/suggested_fix）
        classified = _error_classify(state)
        sub_id = str(state.get("current_sub_task_id") or "") or "unknown"
        sub_task = _sub_task_for(state, sub_id)
        retry_counts = dict(state.get("retry_counts") or {})
        retry_count = int(retry_counts.get(sub_id, 0))
        fingerprints = list(state.get("error_fingerprints") or [])
        llm_used = int(state.get("llm_calls_used") or 0)

        # 策略开关关掉 Reflector：直接当熔断，去报告（不做自愈）
        if not flags.enable_reflector:
            melted = ReflectResp(
                should_retry=False,
                circuit_breaker=True,
                melt_reason="retry_limit",
                fix_hint=None,
                final_status="melted",
            )
            results = _mark_status(
                list(state.get("sub_task_results") or []), sub_id, "failed"
            )
            return {
                "reflect": melted,
                "sub_task_results": results,
                "retry_counts": retry_counts,
                "next_node": "report",
            }

        req = ReflectReq(
            sub_task=sub_task,
            error_classify=classified,
            retry_count=retry_count,
            max_retry=self._settings.max_retry_rounds,
            llm_calls_used=llm_used,
            max_llm_calls=self._settings.max_llm_calls_per_task,
            previous_fingerprints=fingerprints,
        )
        resp = self._reflector.reflect(req)
        results = list(state.get("sub_task_results") or [])
        # Reflect 若用 LLM 润色了 hint，要把这次调用计入任务预算
        llm_delta = int(getattr(self._reflector, "last_llm_calls", 0) or 0)
        llm_after = llm_used + llm_delta

        if resp.should_retry:
            # 自愈：子任务标 healing，把 fix_hint 塞进状态，边路由回 generate_op
            retry_counts[sub_id] = retry_count + 1
            results = _mark_status(results, sub_id, "healing")
            return {
                "reflect": resp,
                "retry_counts": retry_counts,
                "sub_task_results": results,
                "fix_hint": resp.fix_hint,
                "llm_calls_used": llm_after,
                "next_node": "generate_op",
            }

        # Melt or hard fail → report; keep fingerprint history intact.
        results = _mark_status(results, sub_id, "failed")
        return {
            "reflect": resp,
            "retry_counts": retry_counts,
            "sub_task_results": results,
            "llm_calls_used": llm_after,
            "next_node": "report",
        }


def _error_classify(state: TaskState) -> ErrorClassifyResp:
    """从状态取出分类结果；缺失时给一个可重试的兜底 runtime。"""
    bag = cast(Mapping[str, Any], state)
    raw = bag.get("error_classify")
    if raw is None:
        return ErrorClassifyResp(
            error_type="runtime",
            error_detail="missing error_classify",
            error_fingerprint="runtime|missing_error_classify",
            retryable=True,
            suggested_fix="Regenerate the failed operation.",
        )
    if isinstance(raw, ErrorClassifyResp):
        return raw
    return ErrorClassifyResp.model_validate(raw)


def _sub_task_for(state: TaskState, sub_id: str) -> SubTask:
    """按 current_sub_task_id 从 plan 里找到对应子任务描述。"""
    plan_raw = state.get("plan")
    if plan_raw is not None:
        plan = (
            plan_raw
            if isinstance(plan_raw, PlanResp)
            else PlanResp.model_validate(plan_raw)
        )
        for item in plan.sub_tasks:
            if item.task_id == sub_id:
                return item
    return SubTask(task_id=sub_id, description=f"sub-task {sub_id}")


def _mark_status(
    existing: list[SubTaskResult],
    sub_id: str,
    status: Literal["pending", "running", "success", "failed", "healing", "terminated"],
) -> list[SubTaskResult]:
    """更新某个子任务在结果列表里的 status（healing / failed 等）。"""
    out: list[SubTaskResult] = []
    found = False
    for item in existing:
        if item.sub_task_id == sub_id:
            out.append(item.model_copy(update={"status": status}))
            found = True
        else:
            out.append(item)
    if not found:
        out.append(SubTaskResult(sub_task_id=sub_id, status=status))
    return out
