# -*- coding: utf-8 -*-
"""Graph 节点 ``Node_SafetyCheck``：对 ``current_op`` 做 AST 安全门禁。

上游：generate_op。
下游业务：``modules/safety_check``（SQL/Python AST，禁止纯关键字匹配）。
``next_node``：通过 → ``execute``；危险/越权/不可解析 → ``report``（不进人审放行）。

``enable_human_confirm`` 不能削弱本门禁。

See ``tasks.md`` T25 / D-SAFE-01。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, Protocol, cast

from data_reflex.graph_engine.nodes.base import BaseNode
from data_reflex.models.schemas import GenerateOpResp, SafetyCheckReq, SafetyCheckResp
from data_reflex.models.state import StrategyFlags, TaskState
from data_reflex.modules.audit.store import AuditStore
from data_reflex.modules.safety_check.checker import SafetyChecker
from data_reflex.utils.tracing import Tracer

# v1 fixed three-table column whitelist (schema.md). Used when state has no
# richer schema list — never empty (empty would only block SELECT *).
_DEFAULT_ALLOWED_COLUMNS: tuple[str, ...] = (
    # orders
    "order_id",
    "user_id",
    "product_id",
    "product_name",
    "channel",
    "order_date",
    "amount",
    "quantity",
    "is_promo",
    # returns
    "return_id",
    "return_date",
    "return_amount",
    "return_reason",
    # users (non-sensitive analysis columns; phone/email omitted from default
    # allow-list so accidental SELECT phone is unauthorized_column)
    "user_name",
    "user_segment",
    "register_date",
    "city",
)


class _CheckerLike(Protocol):
    def check(self, req: SafetyCheckReq) -> SafetyCheckResp: ...


class NodeSafetyCheck(BaseNode):
    """检查 current_op；违规直送 report，绝不因人审开关放行。"""

    name: ClassVar[str] = "Node_SafetyCheck"

    def __init__(
        self,
        *,
        audit_store: AuditStore | None = None,
        tracer: Tracer | None = None,
        checker: _CheckerLike | None = None,
        allowed_columns: list[str] | None = None,
        max_attempts: int = 2,
    ) -> None:
        super().__init__(
            audit_store=audit_store, tracer=tracer, max_attempts=max_attempts
        )
        self._checker: _CheckerLike = checker or SafetyChecker()
        self._allowed_columns = list(allowed_columns or _DEFAULT_ALLOWED_COLUMNS)

    def execute(self, state: TaskState) -> dict[str, Any]:
        # Human-confirm flag must NOT weaken safety (T25 acceptance).
        flags = state.get("strategy_flags") or StrategyFlags()
        if not isinstance(flags, StrategyFlags):
            flags = StrategyFlags.model_validate(flags)
        _ = flags.enable_human_confirm  # explicitly ignored for gating

        op = _current_op(state)
        if op is None:
            empty = SafetyCheckResp(
                passed=False,
                violation_type="unparseable",
                violation_detail="no current_op to safety-check",
            )
            return {
                "safety_check": empty,
                "next_node": "report",
            }

        allowed = _allowed_columns_for(state, self._allowed_columns)
        resp = self._checker.check(
            SafetyCheckReq(
                op_type=op.op_type,
                op_content=op.op_content,
                allowed_columns=allowed,
            )
        )

        if resp.passed:
            return {
                "safety_check": resp,
                "current_op": op,
                "next_node": "execute",
            }

        # Dangerous / unauthorized / unparseable → report; never human-confirm.
        return {
            "safety_check": resp,
            "current_op": op,
            "next_node": "report",
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


def _allowed_columns_for(state: TaskState, default: list[str]) -> list[str]:
    """Prefer explicit allow-list on state; else schema default."""
    override = cast(Mapping[str, Any], state).get("allowed_columns")
    if isinstance(override, list) and override:
        return [str(c) for c in override]
    return list(default)
