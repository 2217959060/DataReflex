# -*- coding: utf-8 -*-
"""Graph 节点 ``Node_Plan``：把范围内意图拆成有序 SubTask。

上游：intent_parse（in_scope 且无需澄清）。
下游业务：``modules/plan/builder``（规则模板，不调 LLM）。
本节点一般不写 ``next_node``；固定边进入 plan_validate。

``enable_planner=False`` 时退化为单步探测计划（同图不同策略）。

See ``tasks.md`` T21 / D-PLAN-01。
"""

from __future__ import annotations

from typing import Any, ClassVar

from data_reflex.graph_engine.nodes.base import BaseNode
from data_reflex.models.schemas import IntentParseResp, PlanReq, PlanResp
from data_reflex.models.state import StrategyFlags, TaskState
from data_reflex.modules.audit.store import AuditStore
from data_reflex.modules.plan.builder import PlanBuilder
from data_reflex.utils.tracing import Tracer


class NodePlan(BaseNode):
    """生成 ``plan``；非法意图时写失败 PlanResp 供后续校验/报告。"""

    name: ClassVar[str] = "Node_Plan"

    def __init__(
        self,
        *,
        audit_store: AuditStore | None = None,
        tracer: Tracer | None = None,
        builder: PlanBuilder | None = None,
        max_attempts: int = 2,
    ) -> None:
        super().__init__(
            audit_store=audit_store, tracer=tracer, max_attempts=max_attempts
        )
        self._builder = builder or PlanBuilder()

    def execute(self, state: TaskState) -> dict[str, Any]:
        intent_raw = state.get("intent")
        dataset_id = str(state.get("dataset_id") or "")
        conversation = list(state.get("conversation_context") or [])

        if intent_raw is None:
            return {
                "plan": PlanResp(
                    success=False,
                    sub_tasks=[],
                    error_msg="cannot plan: intent is missing",
                )
            }

        intent = (
            intent_raw
            if isinstance(intent_raw, IntentParseResp)
            else IntentParseResp.model_validate(intent_raw)
        )

        if not intent.in_scope or intent.need_clarification:
            reason = intent.reject_reason or (
                "clarification required before planning"
                if intent.need_clarification
                else "intent out of scope"
            )
            return {
                "plan": PlanResp(
                    success=False,
                    sub_tasks=[],
                    error_msg=f"cannot plan: {reason}",
                )
            }

        flags = state.get("strategy_flags") or StrategyFlags()
        if not isinstance(flags, StrategyFlags):
            flags = StrategyFlags.model_validate(flags)

        req = PlanReq(
            dataset_id=dataset_id,
            normalized_query=intent.normalized_query or "",
            constraints=list(intent.constraints),
            conversation_context=conversation,
        )
        # enable_planner=False → single-step probe (same graph, one SubTask).
        plan = self._builder.build(
            req,
            question_tag=intent.question_tag,
            single_step=not flags.enable_planner,
        )
        return {"plan": plan}
