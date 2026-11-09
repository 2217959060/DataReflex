# -*- coding: utf-8 -*-
"""任务 HTTP API：提交、SSE 订阅、人审确认、终止、trace 查询。

上游：前端 / 评测客户端。
本文件：薄路由，业务交给 ``TaskQueue`` / runtime。
下游：队列驱动 Graph；SSE 读 ``TaskEventLog``。

前缀：``/api/tasks``。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from data_reflex.api.deps import AppRuntime, get_runtime
from data_reflex.exceptions import TaskQueueError
from data_reflex.models.state import (
    ConfirmReq,
    StrategyFlags,
    TaskEvent,
    TaskSubmitReq,
    TaskSubmitResp,
    TerminateReq,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_ACTIVE = frozenset({"queued", "running", "waiting_confirm", "pending"})


@router.post("", response_model=TaskSubmitResp)
def submit_task(
    body: TaskSubmitReq,
    runtime: Annotated[AppRuntime, Depends(get_runtime)],
) -> TaskSubmitResp:
    flags = body.strategy_flags or StrategyFlags()
    resp = runtime.queue.submit(
        dataset_id=body.dataset_id,
        user_query=body.user_query,
        strategy_flags=flags,
        conversation_context=list(body.conversation_context or []),
    )
    if not resp.accepted:
        raise TaskQueueError(resp.reject_reason or "task rejected")
    return resp


@router.get("/{task_id}/stream")
async def stream_task_events(
    task_id: str,
    request: Request,
    runtime: Annotated[AppRuntime, Depends(get_runtime)],
    after_seq: Annotated[int, Query(ge=-1)] = -1,
) -> EventSourceResponse:
    handle = runtime.queue.get(task_id)
    if handle is None:
        raise HTTPException(status_code=404, detail=f"unknown task_id: {task_id}")

    async def _gen() -> AsyncIterator[dict[str, str]]:
        cursor = after_seq
        while True:
            if await request.is_disconnected():
                break
            batch = handle.events.snapshot(after_seq=cursor)
            if batch:
                for ev in batch:
                    yield _sse_item(ev)
                    cursor = ev.seq
                continue
            if handle.status not in _ACTIVE:
                # Final drain after terminal status.
                batch = handle.events.snapshot(after_seq=cursor)
                for ev in batch:
                    yield _sse_item(ev)
                    cursor = ev.seq
                break
            await asyncio.sleep(0.05)

    return EventSourceResponse(_gen())


def _sse_item(ev: TaskEvent) -> dict[str, str]:
    return {
        "id": str(ev.seq),
        "event": ev.event_type,
        "data": json.dumps(ev.model_dump(), ensure_ascii=False),
    }


@router.post("/{task_id}/confirm-plan")
def confirm_plan(
    task_id: str,
    body: ConfirmReq,
    runtime: Annotated[AppRuntime, Depends(get_runtime)],
) -> dict[str, Any]:
    if body.task_id and body.task_id != task_id:
        raise TaskQueueError("task_id in path and body must match")
    req = ConfirmReq(
        task_id=task_id,
        confirm_type=body.confirm_type,
        decision=body.decision,
        idempotency_key=body.idempotency_key,
    )
    try:
        record = runtime.queue.confirm(req)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "task_id": record.task_id,
        "decision": record.decision,
        "idempotency_key": record.idempotency_key,
        "mutated": record.mutated,
    }


@router.post("/{task_id}/terminate")
def terminate_task(
    task_id: str,
    body: TerminateReq,
    runtime: Annotated[AppRuntime, Depends(get_runtime)],
) -> dict[str, Any]:
    if body.task_id and body.task_id != task_id:
        raise TaskQueueError("task_id in path and body must match")
    try:
        handle = runtime.queue.terminate(task_id, body.reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    exe_terminated = False
    if handle.executor is not None:
        exe_terminated = bool(getattr(handle.executor, "terminated", False))
    return {
        "task_id": handle.task_id,
        "status": handle.status,
        "reason": handle.terminate_reason,
        "executor_terminated": exe_terminated,
    }


@router.get("/{task_id}/trace")
def get_trace(
    task_id: str,
    runtime: Annotated[AppRuntime, Depends(get_runtime)],
) -> dict[str, Any]:
    handle = runtime.queue.get(task_id)
    if handle is None:
        raise HTTPException(status_code=404, detail=f"unknown task_id: {task_id}")
    audit = [
        {
            "id": e.id,
            "event_type": e.event_type,
            "payload": e.payload,
            "created_at": e.created_at,
        }
        for e in runtime.audit_store.list_audit_events(task_id)
    ]
    events = [e.model_dump() for e in handle.events.snapshot(after_seq=-1)]
    results: list[Any] = []
    final_report: Any = None
    fallback_report: Any = None
    plan: Any = None
    intent: Any = None
    if handle.final_state:
        raw = handle.final_state.get("sub_task_results") or []
        results = [r.model_dump() if hasattr(r, "model_dump") else r for r in raw]
        fr = handle.final_state.get("final_report")
        if fr is not None:
            final_report = fr.model_dump() if hasattr(fr, "model_dump") else fr
        fb = handle.final_state.get("fallback_report")
        if fb is not None:
            fallback_report = fb.model_dump() if hasattr(fb, "model_dump") else fb
        pl = handle.final_state.get("plan")
        if pl is not None:
            plan = pl.model_dump() if hasattr(pl, "model_dump") else pl
        it = handle.final_state.get("intent")
        if it is not None:
            intent = it.model_dump() if hasattr(it, "model_dump") else it
    return {
        "task_id": task_id,
        "status": handle.status,
        "terminate_reason": handle.terminate_reason,
        "events": events,
        "audit_events": audit,
        "sub_task_results": results,
        "final_report": final_report,
        "fallback_report": fallback_report,
        "plan": plan,
        "intent": intent,
        "queue_position": handle.queue_position,
        "user_query": handle.user_query,
        "dataset_id": handle.dataset_id,
    }
