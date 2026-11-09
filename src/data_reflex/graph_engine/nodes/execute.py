# -*- coding: utf-8 -*-
"""Graph 节点 ``Node_Execute``：在沙箱执行 ``current_op``（宿主机不跑生成代码）。

上游：safety_check 通过。
下游业务：默认 ``DockerSandboxExecutor``（或测试注入 Mock）。
``next_node``：
  - 成功 → ``result_validate``
  - 软失败 / 资源杀 → ``error_classify``（由分类器决定能否 Reflect）
  - 无 op → ``report``

``SandboxUnavailableError`` 向上抛，不降级到本机执行。

See ``tasks.md`` T26 / D-DOCKER-01。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, Protocol, cast

from data_reflex.config.settings import Settings, get_settings
from data_reflex.graph_engine.nodes.base import BaseNode, truncate_error_text
from data_reflex.models.schemas import (
    ExecuteReq,
    ExecuteResp,
    GenerateOpResp,
    SubTaskResult,
)
from data_reflex.models.state import TaskState
from data_reflex.modules.audit.store import AuditStore
from data_reflex.modules.executor.docker_executor import DockerSandboxExecutor
from data_reflex.utils.tracing import Tracer


class _ExecutorLike(Protocol):
    def execute(self, req: ExecuteReq) -> ExecuteResp: ...


class NodeExecute(BaseNode):
    """沙箱执行；成功校验、失败进分类。资源杀标 failed 仍走 error_classify 打指纹。"""

    name: ClassVar[str] = "Node_Execute"

    def __init__(
        self,
        *,
        audit_store: AuditStore | None = None,
        tracer: Tracer | None = None,
        executor: _ExecutorLike | None = None,
        settings: Settings | None = None,
        max_attempts: int = 2,
    ) -> None:
        super().__init__(
            audit_store=audit_store, tracer=tracer, max_attempts=max_attempts
        )
        self._settings = settings if settings is not None else get_settings()
        self._executor: _ExecutorLike = executor or DockerSandboxExecutor(
            settings=self._settings
        )

    def execute(self, state: TaskState) -> dict[str, Any]:
        sub_id = str(state.get("current_sub_task_id") or "") or "unknown"
        existing = list(state.get("sub_task_results") or [])

        # V2-3: redundant compare steps reuse prior aggregation rows (no sandbox).
        reuse = _reuse_source_id(state, sub_id)
        if reuse is not None:
            prior = next((r for r in existing if r.sub_task_id == reuse), None)
            if prior is not None and prior.status == "success":
                resp = ExecuteResp(
                    success=True,
                    result_rows=list(prior.result_rows),
                    result_columns=list(prior.result_columns),
                    stdout=f"reused:{reuse}",
                    stderr="",
                    exit_code=0,
                    duration_ms=0,
                    resource_killed=False,
                )
                results = _upsert_result(
                    existing,
                    SubTaskResult(
                        sub_task_id=sub_id,
                        status="success",
                        result_rows=list(prior.result_rows),
                        result_columns=list(prior.result_columns),
                        duration_ms=0,
                    ),
                )
                return {
                    "execute_resp": resp,
                    "current_op": _current_op(state),
                    "sub_task_results": results,
                    "next_node": "result_validate",
                }
            empty = ExecuteResp(
                success=False,
                stderr=f"reuse source missing or failed: {reuse}",
                exit_code=1,
            )
            return {
                "execute_resp": empty,
                "current_op": _current_op(state),
                "sub_task_results": existing,
                "next_node": "error_classify",
            }

        op = _current_op(state)
        if op is None:
            empty = ExecuteResp(
                success=False,
                stderr="no current_op to execute",
                exit_code=1,
            )
            return {
                "execute_resp": empty,
                "next_node": "report",
            }

        dataset_id = str(state.get("dataset_id") or "")
        req = ExecuteReq(
            op_type=op.op_type,
            op_content=op.op_content,
            dataset_id=dataset_id,
            timeout_seconds=self._settings.sandbox_timeout_seconds,
            memory_limit_mb=self._settings.sandbox_memory_limit_mb,
            cpu_limit=self._settings.sandbox_cpu_limit,
        )

        # SandboxUnavailableError (and other hard failures) propagate — BaseNode
        # may retry once; never degrade to host execution.
        resp = self._executor.execute(req)

        if resp.success:
            results = _upsert_result(
                existing,
                SubTaskResult(
                    sub_task_id=sub_id,
                    status="success",
                    result_rows=list(resp.result_rows),
                    result_columns=list(resp.result_columns),
                    duration_ms=resp.duration_ms,
                ),
            )
            return {
                "execute_resp": resp,
                "current_op": op,
                "sub_task_results": results,
                "next_node": "result_validate",
            }

        if resp.resource_killed:
            # T26: resource over-limit → mark this sub-task failed immediately.
            # Still route to error_classify so T27 can fingerprint (retryable=False).
            summary = truncate_error_text(resp.stderr or "resource_killed")
            results = _upsert_result(
                existing,
                SubTaskResult(
                    sub_task_id=sub_id,
                    status="failed",
                    error_summary=summary,
                    duration_ms=resp.duration_ms,
                ),
            )
            return {
                "execute_resp": resp,
                "current_op": op,
                "sub_task_results": results,
                "next_node": "error_classify",
            }

        # Soft failure (syntax/runtime/…) — ErrorClassify / Reflect may heal.
        return {
            "execute_resp": resp,
            "current_op": op,
            "sub_task_results": existing,
            "next_node": "error_classify",
        }


def _current_op(state: TaskState) -> GenerateOpResp | None:
    bag = cast(Mapping[str, Any], state)
    raw = bag.get("current_op")
    if raw is None:
        ops = bag.get("generated_ops")
        if isinstance(ops, dict) and ops:
            raw = next(iter(ops.values()))
    if raw is None:
        return None
    if isinstance(raw, GenerateOpResp):
        return raw
    return GenerateOpResp.model_validate(raw)


def _reuse_source_id(state: TaskState, sub_id: str) -> str | None:
    """Return prior sub-task id when plan marks ``reuse_result``, else None."""
    from data_reflex.models.schemas import PlanResp

    plan_raw = state.get("plan")
    if plan_raw is None:
        return None
    plan = (
        plan_raw
        if isinstance(plan_raw, PlanResp)
        else PlanResp.model_validate(plan_raw)
    )
    current = next((st for st in plan.sub_tasks if st.task_id == sub_id), None)
    if current is None or not current.expected_input.get("reuse_result"):
        return None
    source_id = str(
        current.expected_input.get("from_sub_task")
        or (current.depends_on[0] if current.depends_on else "")
    )
    return source_id or None


def _upsert_result(
    existing: list[SubTaskResult], result: SubTaskResult
) -> list[SubTaskResult]:
    out: list[SubTaskResult] = []
    replaced = False
    for item in existing:
        if item.sub_task_id == result.sub_task_id:
            out.append(result)
            replaced = True
        else:
            out.append(item)
    if not replaced:
        out.append(result)
    return out
