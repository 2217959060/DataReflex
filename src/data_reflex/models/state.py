# -*- coding: utf-8 -*-
"""任务状态、策略开关与 API 事件模型。

``TaskState`` 是 Graph/队列的核心状态；运行时额外路由键见 ``graph_engine.state.GraphState``。
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict
from uuid import uuid4

from pydantic import Field

from data_reflex.models.base import StrictModel
from data_reflex.models.schemas import (
    FallbackReportResp,
    IntentParseResp,
    PlanResp,
    ReportResp,
    SubTaskResult,
)

TaskStatus = Literal[
    "pending",
    "running",
    "waiting_confirm",
    "success",
    "failed",
    "terminated",
    "fallback",
]


class StrategyFlags(StrictModel):
    """Runtime strategy switches — defaults match docs/defaults.md."""

    enable_planner: bool = True
    enable_reflector: bool = True
    enable_sandbox: bool = True
    enable_human_confirm: bool = True
    enable_fallback_report: bool = True


class ContextManageReq(StrictModel):
    dataset_id: str
    previous_dataset_id: str | None = None
    conversation_context: list[dict[str, Any]] = Field(default_factory=list)
    user_query: str


class ContextManageResp(StrictModel):
    active_context: list[dict[str, Any]] = Field(default_factory=list)
    is_new_topic: bool = False
    context_cleared: bool = False
    clarification_required: bool = False
    clarification_questions: list[str] = Field(default_factory=list)


class TaskSubmitReq(StrictModel):
    dataset_id: str
    user_query: str
    strategy_flags: StrategyFlags = Field(default_factory=StrategyFlags)
    # V2-5 / D-V2-FOLLOWUP-01: client-carried last 1–2 turns (D-CTX-01 keys).
    conversation_context: list[dict[str, Any]] = Field(default_factory=list)


class TaskSubmitResp(StrictModel):
    task_id: str
    queue_position: int = Field(ge=0)
    accepted: bool
    reject_reason: str | None = None


class TaskEvent(StrictModel):
    task_id: str
    seq: int = Field(ge=0)
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str


class ConfirmReq(StrictModel):
    task_id: str
    confirm_type: Literal["plan", "patch"]
    decision: Literal["accept", "reject"]
    idempotency_key: str


class TerminateReq(StrictModel):
    task_id: str
    reason: str


class TaskState(TypedDict):
    """LangGraph global state — fields fixed by tasks.md T07."""

    task_id: str
    dataset_id: str
    user_query: str
    conversation_context: list[dict[str, Any]]
    intent: IntentParseResp | None
    plan: PlanResp | None
    sub_task_results: list[SubTaskResult]
    current_sub_task_id: str | None
    retry_counts: dict[str, int]
    error_fingerprints: list[str]
    llm_calls_used: int
    final_report: ReportResp | None
    fallback_report: FallbackReportResp | None
    strategy_flags: StrategyFlags
    audit_trace_id: str
    status: TaskStatus


def create_initial_task_state(
    *,
    dataset_id: str,
    user_query: str,
    strategy_flags: StrategyFlags | None = None,
    task_id: str | None = None,
    audit_trace_id: str | None = None,
    conversation_context: list[dict[str, Any]] | None = None,
) -> TaskState:
    """Factory used by API/queue to seed a pending task."""
    return TaskState(
        task_id=task_id or str(uuid4()),
        dataset_id=dataset_id,
        user_query=user_query,
        conversation_context=list(conversation_context or []),
        intent=None,
        plan=None,
        sub_task_results=[],
        current_sub_task_id=None,
        retry_counts={},
        error_fingerprints=[],
        llm_calls_used=0,
        final_report=None,
        fallback_report=None,
        strategy_flags=strategy_flags or StrategyFlags(),
        audit_trace_id=audit_trace_id or str(uuid4()),
        status="pending",
    )
