# -*- coding: utf-8 -*-
"""Graph 节点 ``Node_HumanConfirmPlan``：计划人工确认（接受/拒绝/超时）。

上游：plan_validate（``next_node=human_confirm``）。
下游业务：``modules/human_confirm/gate``（幂等 + 超时当拒绝）。
路由：
  - 开关关闭 → 直接 ``generate_op``
  - 尚无决定 → ``status=waiting_confirm``，边路由到 END（挂起等 API resume）
  - accept → ``generate_op``；reject/timeout → ``report``
拒绝不清理已完成子任务结果。

See ``tasks.md`` T23 / D-CONFIRM-01。
"""

from __future__ import annotations

from typing import Any, ClassVar

from data_reflex.config.settings import Settings, get_settings
from data_reflex.graph_engine.nodes.base import BaseNode
from data_reflex.models.state import ConfirmReq, StrategyFlags, TaskState
from data_reflex.modules.audit.store import AuditStore
from data_reflex.modules.human_confirm.gate import ConfirmRecord, PlanConfirmGate
from data_reflex.utils.tracing import Tracer


class NodeHumanConfirmPlan(BaseNode):
    """等人审；写 ``next_node`` / ``waiting_confirm``。拒绝时保留已有结果。"""

    name: ClassVar[str] = "Node_HumanConfirmPlan"

    def __init__(
        self,
        *,
        audit_store: AuditStore | None = None,
        tracer: Tracer | None = None,
        confirm_gate: PlanConfirmGate | None = None,
        settings: Settings | None = None,
        max_attempts: int = 2,
    ) -> None:
        super().__init__(
            audit_store=audit_store, tracer=tracer, max_attempts=max_attempts
        )
        cfg = settings if settings is not None else get_settings()
        self._gate = confirm_gate or PlanConfirmGate(
            timeout_seconds=cfg.plan_confirm_timeout_seconds
        )

    @property
    def confirm_gate(self) -> PlanConfirmGate:
        return self._gate

    def apply_confirm(self, req: ConfirmReq) -> ConfirmRecord:
        """API/resume entry: submit an idempotent confirm decision."""
        return self._gate.submit(req)

    def execute(self, state: TaskState) -> dict[str, Any]:
        flags = state.get("strategy_flags") or StrategyFlags()
        if not isinstance(flags, StrategyFlags):
            flags = StrategyFlags.model_validate(flags)

        # Preserve existing results — never clear on any branch.
        existing_results = list(state.get("sub_task_results") or [])

        if not flags.enable_human_confirm:
            return {
                "status": "running",
                "next_node": "generate_op",
                "sub_task_results": existing_results,
            }

        task_id = str(state.get("task_id") or "")
        self._gate.ensure_waiting(task_id)
        outcome = self._gate.resolve(task_id)

        if outcome is None:
            return {
                "status": "waiting_confirm",
                "sub_task_results": existing_results,
            }

        if outcome.decision == "accept":
            return {
                "status": "running",
                "next_node": "generate_op",
                "sub_task_results": existing_results,
            }

        # reject or timeout → report; keep completed results
        return {
            "status": "running",
            "next_node": "report",
            "sub_task_results": existing_results,
        }
