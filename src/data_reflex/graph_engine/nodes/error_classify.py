# -*- coding: utf-8 -*-
"""Graph 节点 ``Node_ErrorClassify``：给执行失败打类型与指纹，决定能否进 Reflect。

上游：execute 失败 / generate_op 格式耗尽等。
下游业务：``modules/error_classify/classifier``（产出 suggested_fix）。
``next_node``：可重试且 ``enable_reflector`` → ``reflect``；否则 ``report``。
熔断（次数/重复指纹/LLM 上限）在 ``Node_Reflect``，本节点只分类+记账。

See ``tasks.md`` T27 / D-ERRCLS-01。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, Protocol, cast

from data_reflex.graph_engine.nodes.base import BaseNode, truncate_error_text
from data_reflex.models.schemas import (
    ErrorClassifyReq,
    ErrorClassifyResp,
    ExecuteResp,
    GenerateOpResp,
    SubTaskResult,
)
from data_reflex.models.state import StrategyFlags, TaskState
from data_reflex.modules.audit.store import AuditStore
from data_reflex.modules.error_classify.classifier import ErrorClassifier
from data_reflex.utils.tracing import Tracer


class _ClassifierLike(Protocol):
    def classify(self, req: ErrorClassifyReq) -> ErrorClassifyResp: ...


class NodeErrorClassify(BaseNode):
    """分类 execute_resp，追加指纹；可愈则 reflect，否则 report。"""

    name: ClassVar[str] = "Node_ErrorClassify"

    def __init__(
        self,
        *,
        audit_store: AuditStore | None = None,
        tracer: Tracer | None = None,
        classifier: _ClassifierLike | None = None,
        max_attempts: int = 2,
    ) -> None:
        super().__init__(
            audit_store=audit_store, tracer=tracer, max_attempts=max_attempts
        )
        self._classifier: _ClassifierLike = classifier or ErrorClassifier()

    def execute(self, state: TaskState) -> dict[str, Any]:
        op = _current_op(state)
        op_type = op.op_type if op is not None else "sql"
        execute_resp = _execute_resp(state, op)

        classified = self._classifier.classify(
            ErrorClassifyReq(op_type=op_type, execute_resp=execute_resp)
        )

        fingerprints = list(state.get("error_fingerprints") or [])
        if classified.error_type != "none" and classified.error_fingerprint:
            fingerprints.append(classified.error_fingerprint)

        sub_id = str(state.get("current_sub_task_id") or "") or "unknown"
        existing = list(state.get("sub_task_results") or [])
        results = _record_subtask_error(existing, sub_id, classified, execute_resp)

        flags = state.get("strategy_flags") or StrategyFlags()
        if not isinstance(flags, StrategyFlags):
            flags = StrategyFlags.model_validate(flags)

        # Design: retryable → Reflect; not retryable (incl. resource_killed) → Report.
        # enable_reflector=False → fail straight to Report (no heal loop).
        # Circuit-breaker / call-cap melt is owned by Node_Reflect (T28).
        if (
            flags.enable_reflector
            and classified.retryable
            and classified.error_type != "none"
        ):
            next_node = "reflect"
        else:
            next_node = "report"

        return {
            "error_classify": classified,
            "execute_resp": execute_resp,
            "error_fingerprints": fingerprints,
            "sub_task_results": results,
            "next_node": next_node,
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


def _execute_resp(state: TaskState, op: GenerateOpResp | None) -> ExecuteResp:
    bag = cast(Mapping[str, Any], state)
    raw = bag.get("execute_resp")
    if raw is not None:
        if isinstance(raw, ExecuteResp):
            return raw
        return ExecuteResp.model_validate(raw)

    # Format-exhausted GenerateOp may route here without an execute_resp.
    detail = "no execute_resp for error classify"
    if op is not None and op.error_msg:
        detail = op.error_msg
    else:
        for item in state.get("sub_task_results") or []:
            if item.status == "failed" and item.error_summary:
                detail = item.error_summary
                break
    return ExecuteResp(success=False, stderr=detail, exit_code=1)


def _record_subtask_error(
    existing: list[SubTaskResult],
    sub_id: str,
    classified: ErrorClassifyResp,
    execute_resp: ExecuteResp,
) -> list[SubTaskResult]:
    """Write classification into the current sub-task error record."""
    summary = truncate_error_text(
        classified.error_detail
        or classified.error_fingerprint
        or execute_resp.stderr
        or classified.error_type
    )
    # Preserve any prior rows; mark failed so report/reflect see the attempt.
    prior = next((r for r in existing if r.sub_task_id == sub_id), None)
    updated = SubTaskResult(
        sub_task_id=sub_id,
        status="failed",
        result_rows=list(prior.result_rows) if prior else [],
        result_columns=list(prior.result_columns) if prior else [],
        error_summary=summary,
        duration_ms=(
            execute_resp.duration_ms
            if execute_resp.duration_ms
            else (prior.duration_ms if prior else 0)
        ),
    )
    out: list[SubTaskResult] = []
    replaced = False
    for item in existing:
        if item.sub_task_id == sub_id:
            out.append(updated)
            replaced = True
        else:
            out.append(item)
    if not replaced:
        out.append(updated)
    return out
