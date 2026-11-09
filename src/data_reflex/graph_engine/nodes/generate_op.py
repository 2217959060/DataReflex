# -*- coding: utf-8 -*-
"""Graph 节点 ``Node_GenerateOp``：为当前并行批次生成 SQL/Python。

上游：human_confirm 通过 / plan_validate 跳过人审 / Reflect 自愈回环 /
      result_validate 发现还有 pending 子任务。
下游业务：``modules/generate_op``（模板 + 可选 LLM；失败回退模板）。
写出：``current_op`` / ``generated_ops``，``next_node``=
  ``safety_check``（有合法 op）或 ``error_classify``/``report``（格式全挂）。

格式不合法时本节点内可再试 1 次（≠ Reflect 自愈）。

See ``tasks.md`` T24 / D-GENOP-01。
"""

from __future__ import annotations

from typing import Any, ClassVar, Protocol

from data_reflex.graph_engine.nodes.base import BaseNode
from data_reflex.models.schemas import (
    GenerateOpReq,
    GenerateOpResp,
    PlanResp,
    SubTask,
    SubTaskResult,
)
from data_reflex.models.state import TaskState
from data_reflex.modules.audit.store import AuditStore
from data_reflex.modules.generate_op.generator import OpGenerator
from data_reflex.utils.tracing import Tracer

# 单次生成格式门：首次 + 再试 1 次（设计/T24）；与 Reflect 回环无关
_FORMAT_MAX_ATTEMPTS = 2


class _GeneratorLike(Protocol):
    def generate(self, req: GenerateOpReq) -> GenerateOpResp: ...


class NodeGenerateOp(BaseNode):
    """按最低未完成 parallel_group 批量生成；合法则进安全检查。"""

    name: ClassVar[str] = "Node_GenerateOp"

    def __init__(
        self,
        *,
        audit_store: AuditStore | None = None,
        tracer: Tracer | None = None,
        generator: _GeneratorLike | None = None,
        max_attempts: int = 2,
        format_max_attempts: int = _FORMAT_MAX_ATTEMPTS,
    ) -> None:
        super().__init__(
            audit_store=audit_store, tracer=tracer, max_attempts=max_attempts
        )
        self._generator: _GeneratorLike = generator or OpGenerator()
        self._format_max_attempts = max(1, format_max_attempts)

    def execute(self, state: TaskState) -> dict[str, Any]:
        plan_raw = state.get("plan")
        if plan_raw is None:
            return {
                "next_node": "report",
                "sub_task_results": list(state.get("sub_task_results") or []),
            }

        plan = (
            plan_raw
            if isinstance(plan_raw, PlanResp)
            else PlanResp.model_validate(plan_raw)
        )
        if not plan.success or not plan.sub_tasks:
            return {
                "next_node": "report",
                "sub_task_results": list(state.get("sub_task_results") or []),
            }

        existing = list(state.get("sub_task_results") or [])
        done_ids = {
            r.sub_task_id
            for r in existing
            if r.status in {"success", "failed", "terminated"}
        }
        batch = _pending_batch(plan.sub_tasks, done_ids)
        constraints = _constraints_from_state(state)

        generated: dict[str, GenerateOpResp] = {}
        new_results = list(existing)
        llm_used = int(state.get("llm_calls_used") or 0)
        llm_delta = 0

        for sub_task in batch:
            resp, calls = self._generate_with_format_retry(
                sub_task, constraints=constraints
            )
            llm_delta += calls
            if resp.format_valid:
                generated[sub_task.task_id] = resp
            else:
                new_results.append(
                    SubTaskResult(
                        sub_task_id=sub_task.task_id,
                        status="failed",
                        error_summary=(
                            resp.error_msg or "op format invalid after retry"
                        )[:240],
                    )
                )

        out_base: dict[str, Any] = {
            "llm_calls_used": llm_used + llm_delta,
        }
        if generated:
            first_id = next(iter(generated))
            return {
                **out_base,
                "sub_task_results": new_results,
                "generated_ops": generated,
                "current_sub_task_id": first_id,
                "current_op": generated[first_id],
                "next_node": "safety_check",
            }

        # Entire batch failed format → classify / report path.
        return {
            **out_base,
            "sub_task_results": new_results,
            "generated_ops": {},
            "current_sub_task_id": batch[0].task_id if batch else None,
            "current_op": None,
            "next_node": "error_classify" if batch else "report",
        }

    def _generate_with_format_retry(
        self,
        sub_task: SubTask,
        *,
        constraints: list[str],
    ) -> tuple[GenerateOpResp, int]:
        previous_error: str | None = None
        last = GenerateOpResp(
            op_type="sql",
            op_content="",
            format_valid=False,
            error_msg="no attempt",
        )
        llm_calls = 0
        for attempt in range(1, self._format_max_attempts + 1):
            req = GenerateOpReq(
                sub_task=sub_task,
                table_schemas=[],
                constraints=constraints,
                previous_error=previous_error,
            )
            last = self._generator.generate(req)
            llm_calls += int(getattr(self._generator, "last_llm_calls", 0) or 0)
            if last.format_valid:
                return last, llm_calls
            previous_error = last.error_msg or f"format invalid attempt={attempt}"
        return last, llm_calls


def _pending_batch(sub_tasks: list[SubTask], done_ids: set[str]) -> list[SubTask]:
    """Return unfinished tasks in the lowest unfinished parallel_group."""
    pending = [t for t in sub_tasks if t.task_id not in done_ids]
    if not pending:
        return []
    group = min(t.parallel_group for t in pending)
    return [t for t in pending if t.parallel_group == group]


def _constraints_from_state(state: TaskState) -> list[str]:
    intent = state.get("intent")
    if intent is None:
        return []
    constraints = getattr(intent, "constraints", None)
    if constraints is None and isinstance(intent, dict):
        constraints = intent.get("constraints")
    return list(constraints or [])
