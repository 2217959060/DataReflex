# -*- coding: utf-8 -*-
"""Graph 节点 ``Node_PlanValidate``：校验计划依赖/完备性，并写出路由。

上游：plan。
下游业务：``modules/plan_validate``。
写出 ``next_node``：``human_confirm`` / ``generate_op`` / ``report``
（由 ``enable_human_confirm`` 与校验结果决定）。

See ``tasks.md`` T22 / D-PLANVAL-01。
"""

from __future__ import annotations

from typing import Any, ClassVar

from data_reflex.graph_engine.nodes.base import BaseNode
from data_reflex.models.schemas import PlanResp
from data_reflex.models.state import StrategyFlags, TaskState
from data_reflex.modules.audit.store import AuditStore
from data_reflex.modules.plan_validate.validator import PlanValidator
from data_reflex.utils.tracing import Tracer


class NodePlanValidate(BaseNode):
    """校验 plan，并把 ``next_node`` 交给 builder 条件边。"""

    name: ClassVar[str] = "Node_PlanValidate"

    def __init__(
        self,
        *,
        audit_store: AuditStore | None = None,
        tracer: Tracer | None = None,
        validator: PlanValidator | None = None,
        max_attempts: int = 2,
    ) -> None:
        super().__init__(
            audit_store=audit_store, tracer=tracer, max_attempts=max_attempts
        )
        self._validator = validator or PlanValidator()

    def execute(self, state: TaskState) -> dict[str, Any]:
        plan_raw = state.get("plan")
        flags = state.get("strategy_flags") or StrategyFlags()
        if not isinstance(flags, StrategyFlags):
            flags = StrategyFlags.model_validate(flags)

        if plan_raw is None:
            empty = PlanResp(
                success=False,
                sub_tasks=[],
                error_msg="cannot validate: plan is missing",
            )
            outcome = self._validator.validate(
                empty, enable_human_confirm=flags.enable_human_confirm
            )
            return {"plan": outcome.plan, "next_node": outcome.next_node}

        plan = (
            plan_raw
            if isinstance(plan_raw, PlanResp)
            else PlanResp.model_validate(plan_raw)
        )
        outcome = self._validator.validate(
            plan, enable_human_confirm=flags.enable_human_confirm
        )
        return {"plan": outcome.plan, "next_node": outcome.next_node}
