# -*- coding: utf-8 -*-
"""Graph 节点 ``Node_ResultValidate``：校验执行结果是否满足结构化约束。

上游：execute 成功。
下游业务：``modules/result_validate``（只认结构化约束，不猜自然语言）。
``next_node``：
  - valid → ``report``（若还有 pending 子任务，builder 边可能再拉回 generate_op）
  - 可自愈异常 → 写 ``fix_hint`` 回 ``generate_op``（不经 Reflect 节点）
  - 不可自愈 → ``report``

与 Reflect 共用指纹/重试/LLM 预算熔断语义。

See ``tasks.md`` T29 / D-RESVAL-01。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, Protocol, cast

from data_reflex.config.settings import Settings, get_settings
from data_reflex.graph_engine.nodes.base import BaseNode, truncate_error_text
from data_reflex.models.schemas import (
    ExecuteResp,
    ResultValidateReq,
    ResultValidateResp,
    SubTaskResult,
)
from data_reflex.models.state import StrategyFlags, TaskState
from data_reflex.modules.audit.store import AuditStore
from data_reflex.modules.error_classify.classifier import normalize_error_line
from data_reflex.modules.result_validate.validator import ResultValidator
from data_reflex.utils.tracing import Tracer

# 仅这些异常类型允许回 generate_op 自愈；其它直接失败收束
_HEALABLE_ANOMALIES = frozenset(
    {
        "empty_anomaly",
        "ratio_out_of_range",
        "type_anomaly",
        "constraint_mismatch",
    }
)


class _ValidatorLike(Protocol):
    def validate(self, req: ResultValidateReq) -> ResultValidateResp: ...


class NodeResultValidate(BaseNode):
    """结果校验；可愈异常带 fix_hint 回 generate_op，否则去 report。"""

    name: ClassVar[str] = "Node_ResultValidate"

    def __init__(
        self,
        *,
        audit_store: AuditStore | None = None,
        tracer: Tracer | None = None,
        validator: _ValidatorLike | None = None,
        settings: Settings | None = None,
        max_attempts: int = 2,
    ) -> None:
        super().__init__(
            audit_store=audit_store, tracer=tracer, max_attempts=max_attempts
        )
        self._settings = settings if settings is not None else get_settings()
        self._validator: _ValidatorLike = validator or ResultValidator()

    def execute(self, state: TaskState) -> dict[str, Any]:
        execute_resp = _execute_resp(state)
        constraints = _structured_constraints(state)
        outcome = self._validator.validate(
            ResultValidateReq(
                execute_resp=execute_resp,
                structured_constraints=constraints,
            )
        )

        if outcome.valid:
            return {
                "result_validate": outcome,
                "execute_resp": execute_resp,
                "next_node": "report",
            }

        # Invalid — decide heal vs report.
        sub_id = str(state.get("current_sub_task_id") or "") or "unknown"
        fingerprints = list(state.get("error_fingerprints") or [])
        fp = _anomaly_fp(outcome)
        if fp:
            fingerprints.append(fp)

        healable = _is_healable(state, outcome, fingerprints, self._settings)
        results = list(state.get("sub_task_results") or [])
        retry_counts = dict(state.get("retry_counts") or {})

        if healable:
            retry_counts[sub_id] = int(retry_counts.get(sub_id, 0)) + 1
            results = _upsert_result(
                results,
                SubTaskResult(
                    sub_task_id=sub_id,
                    status="healing",
                    result_rows=list(execute_resp.result_rows),
                    result_columns=list(execute_resp.result_columns),
                    error_summary=truncate_error_text(
                        outcome.anomaly_detail or outcome.anomaly_type or "anomaly"
                    ),
                    duration_ms=execute_resp.duration_ms,
                ),
            )
            return {
                "result_validate": outcome,
                "execute_resp": execute_resp,
                "error_fingerprints": fingerprints,
                "retry_counts": retry_counts,
                "sub_task_results": results,
                "fix_hint": (
                    f"Fix result anomaly {outcome.anomaly_type}: "
                    f"{outcome.anomaly_detail or ''}"
                ).strip(),
                "next_node": "generate_op",
            }

        results = _upsert_result(
            results,
            SubTaskResult(
                sub_task_id=sub_id,
                status="failed",
                result_rows=list(execute_resp.result_rows),
                result_columns=list(execute_resp.result_columns),
                error_summary=truncate_error_text(
                    outcome.anomaly_detail or outcome.anomaly_type or "anomaly"
                ),
                duration_ms=execute_resp.duration_ms,
            ),
        )
        return {
            "result_validate": outcome,
            "execute_resp": execute_resp,
            "error_fingerprints": fingerprints,
            "retry_counts": retry_counts,
            "sub_task_results": results,
            "next_node": "report",
        }


def _execute_resp(state: TaskState) -> ExecuteResp:
    bag = cast(Mapping[str, Any], state)
    raw = bag.get("execute_resp")
    if raw is None:
        return ExecuteResp(success=False, stderr="no execute_resp", exit_code=1)
    if isinstance(raw, ExecuteResp):
        return raw
    return ExecuteResp.model_validate(raw)


def _structured_constraints(state: TaskState) -> list[dict[str, Any]]:
    """Prefer explicit structured list on state; never invent NL checks."""
    bag = cast(Mapping[str, Any], state)
    raw = bag.get("structured_constraints")
    if isinstance(raw, list):
        out: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, Mapping):
                out.append(dict(item))
        return out
    return []


def _anomaly_fp(outcome: ResultValidateResp) -> str:
    kind = outcome.anomaly_type or "constraint_mismatch"
    detail = normalize_error_line(outcome.anomaly_detail or kind)
    return f"result_anomaly|{kind}|{detail or 'unknown'}"


def _is_healable(
    state: TaskState,
    outcome: ResultValidateResp,
    fingerprints: list[str],
    settings: Settings,
) -> bool:
    if outcome.anomaly_type not in _HEALABLE_ANOMALIES:
        return False

    flags = state.get("strategy_flags") or StrategyFlags()
    if not isinstance(flags, StrategyFlags):
        flags = StrategyFlags.model_validate(flags)
    if not flags.enable_reflector:
        return False

    llm_used = int(state.get("llm_calls_used") or 0)
    if llm_used >= settings.max_llm_calls_per_task:
        return False

    sub_id = str(state.get("current_sub_task_id") or "") or "unknown"
    retry_count = int((state.get("retry_counts") or {}).get(sub_id, 0))
    if retry_count >= settings.max_retry_rounds:
        return False

    # Same consecutive-fingerprint rule as Reflect (after appending current).
    return not (len(fingerprints) >= 2 and fingerprints[-1] == fingerprints[-2])


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
