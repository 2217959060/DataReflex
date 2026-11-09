# -*- coding: utf-8 -*-
"""Graph 节点 ``Node_IntentParse``：范围判断、澄清、显式约束抽取。

上游：START → 本节点（读 user_query / conversation_context）。
下游业务：``modules/intent_parse`` + ``modules/context``。
写出 ``next_node``：``plan``（可分析）或 ``report``（超范围/需澄清）。

需澄清时前端会看到报告里的澄清文案；这不是 Reflect 自愈。

See ``tasks.md`` T20 / D-INTENT-01。
"""

from __future__ import annotations

from typing import Any, ClassVar

from data_reflex.graph_engine.nodes.base import BaseNode
from data_reflex.models.schemas import IntentParseReq
from data_reflex.models.state import TaskState
from data_reflex.modules.audit.store import AuditStore
from data_reflex.modules.context.manager import ContextManager
from data_reflex.modules.intent_parse.parser import IntentParser
from data_reflex.utils.tracing import Tracer


class NodeIntentParse(BaseNode):
    """解析意图并设置 ``next_node``（plan / report）。"""

    name: ClassVar[str] = "Node_IntentParse"

    def __init__(
        self,
        *,
        audit_store: AuditStore | None = None,
        tracer: Tracer | None = None,
        context_manager: ContextManager | None = None,
        parser: IntentParser | None = None,
        max_attempts: int = 2,
    ) -> None:
        super().__init__(
            audit_store=audit_store, tracer=tracer, max_attempts=max_attempts
        )
        self._parser = parser or IntentParser(context_manager=context_manager)

    def execute(self, state: TaskState) -> dict[str, Any]:
        dataset_id = str(state.get("dataset_id") or "")
        user_query = str(state.get("user_query") or "")
        conversation = list(state.get("conversation_context") or [])
        previous_dataset_id = _previous_dataset_id(conversation, dataset_id)

        req = IntentParseReq(
            dataset_id=dataset_id,
            user_query=user_query,
            conversation_context=conversation,
        )
        intent, ctx = self._parser.parse(req, previous_dataset_id=previous_dataset_id)
        llm_used = int(state.get("llm_calls_used") or 0)
        llm_delta = int(getattr(self._parser, "last_llm_calls", 0) or 0)

        # Out-of-scope / clarification → report; in-scope → plan (T32 edges).
        if not intent.in_scope or intent.need_clarification:
            next_node = "report"
        else:
            next_node = "plan"
        return {
            "intent": intent,
            "conversation_context": list(ctx.active_context),
            "llm_calls_used": llm_used + llm_delta,
            "next_node": next_node,
        }


def _previous_dataset_id(
    conversation: list[dict[str, Any]], current_dataset_id: str
) -> str | None:
    """Infer previous dataset from the last turn, if any."""
    _ = current_dataset_id
    if not conversation:
        return None
    last = conversation[-1]
    raw = last.get("dataset_id")
    if raw is None:
        return None
    return str(raw)
