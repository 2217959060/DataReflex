# -*- coding: utf-8 -*-
"""单任务串行队列：受理提问 → 驱动主 LangGraph → SSE/确认/终止。

上游：``api/routes/tasks`` 提交任务。
本文件：全局同时只跑一个分析任务；组装 Graph、Checkpointer、人审门与执行器。
下游：``build_main_graph`` 执行节点；事件写入 ``TaskEventLog``；状态落 ``AuditStore``。

人审挂起时 Graph 停在 waiting_confirm，API confirm 后再 resume。

See ``tasks.md`` 队列相关验收 / architecture 任务流。
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from data_reflex.api.events import TaskEventLog
from data_reflex.graph_engine.builder import (
    MainGraphNodes,
    build_main_graph,
    default_nodes,
    make_thread_config,
)
from data_reflex.graph_engine.nodes.execute import NodeExecute
from data_reflex.graph_engine.nodes.human_confirm import NodeHumanConfirmPlan
from data_reflex.models.state import (
    ConfirmReq,
    StrategyFlags,
    TaskState,
    TaskSubmitResp,
    create_initial_task_state,
)
from data_reflex.modules.audit.store import AuditStore
from data_reflex.modules.executor.base import SandboxExecutor
from data_reflex.modules.executor.mock_executor import MockSandboxExecutor, MockScenario
from data_reflex.modules.human_confirm.gate import ConfirmRecord, PlanConfirmGate
from data_reflex.utils.logger import get_logger
from data_reflex.utils.tracing import clip_query_for_trace, get_tracer

logger = get_logger(__name__)


class _Terminable(Protocol):
    def terminate(self, reason: str = "") -> None: ...


@dataclass
class TaskHandle:
    """已受理任务的运行时句柄（事件流、状态、执行器引用）。"""

    task_id: str
    dataset_id: str
    user_query: str
    strategy_flags: StrategyFlags
    events: TaskEventLog
    initial_state: TaskState
    status: str = "queued"
    queue_position: int = 0
    terminate_reason: str | None = None
    confirm_record: ConfirmRecord | None = None
    confirm_event: threading.Event = field(default_factory=threading.Event)
    stop_event: threading.Event = field(default_factory=threading.Event)
    executor: Any | None = None
    human_confirm: NodeHumanConfirmPlan | None = None
    graph: Any | None = None
    final_state: dict[str, Any] | None = None


ExecutorFactory = Callable[[], SandboxExecutor]


class TaskQueue:
    """v1 串行队列：同时最多一个分析任务在跑。"""

    def __init__(
        self,
        *,
        audit_store: AuditStore | None = None,
        confirm_gate: PlanConfirmGate | None = None,
        executor_factory: ExecutorFactory | None = None,
        checkpointer_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._audit = audit_store
        self._gate = confirm_gate or PlanConfirmGate()
        self._executor_factory = executor_factory or (
            lambda: MockSandboxExecutor(MockScenario.SUCCESS)
        )
        self._checkpointer_factory = checkpointer_factory
        self._lock = threading.RLock()
        self._pending: list[TaskHandle] = []
        self._current: TaskHandle | None = None
        self._handles: dict[str, TaskHandle] = {}
        self._wake = threading.Event()
        self._stopped = False
        self._worker = threading.Thread(
            target=self._worker_loop, name="data-reflex-task-queue", daemon=True
        )
        self._worker.start()

    @property
    def confirm_gate(self) -> PlanConfirmGate:
        return self._gate

    def get(self, task_id: str) -> TaskHandle | None:
        with self._lock:
            return self._handles.get(task_id)

    def submit(
        self,
        *,
        dataset_id: str,
        user_query: str,
        strategy_flags: StrategyFlags | None = None,
        task_id: str | None = None,
        conversation_context: list[dict[str, Any]] | None = None,
    ) -> TaskSubmitResp:
        if self._stopped:
            return TaskSubmitResp(
                task_id="",
                queue_position=0,
                accepted=False,
                reject_reason="queue stopped",
            )
        flags = strategy_flags or StrategyFlags()
        tid = task_id or str(uuid.uuid4())
        state = create_initial_task_state(
            dataset_id=dataset_id,
            user_query=user_query,
            strategy_flags=flags,
            task_id=tid,
            conversation_context=list(conversation_context or []),
        )
        handle = TaskHandle(
            task_id=tid,
            dataset_id=dataset_id,
            user_query=user_query,
            strategy_flags=flags,
            events=TaskEventLog(tid),
            initial_state=state,
        )
        with self._lock:
            ahead = len(self._pending) + (1 if self._current is not None else 0)
            handle.queue_position = ahead
            self._pending.append(handle)
            self._handles[tid] = handle
            if self._audit is not None:
                try:
                    self._audit.save_task(state)
                except Exception:  # noqa: BLE001 — enqueue must not fail on audit
                    logger.warning(
                        "save_task on submit failed",
                        extra={"task_id": tid, "dataset_id": dataset_id},
                    )
        self._wake.set()
        return TaskSubmitResp(
            task_id=tid,
            queue_position=handle.queue_position,
            accepted=True,
            reject_reason=None,
        )

    def confirm(self, req: ConfirmReq) -> ConfirmRecord:
        handle = self.get(req.task_id)
        if handle is None:
            raise KeyError(f"unknown task_id: {req.task_id}")
        node = handle.human_confirm
        # Shared gate — confirm may arrive before the worker binds the node.
        record = self._gate.submit(req) if node is None else node.apply_confirm(req)
        handle.confirm_record = record
        if record.mutated or record.decision in {"accept", "reject", "timeout"}:
            handle.confirm_event.set()
        return record

    def terminate(self, task_id: str, reason: str) -> TaskHandle:
        handle = self.get(task_id)
        if handle is None:
            raise KeyError(f"unknown task_id: {task_id}")
        handle.terminate_reason = reason or "terminated"
        handle.stop_event.set()
        handle.confirm_event.set()
        exe = handle.executor
        if exe is not None and hasattr(exe, "terminate"):
            terminable: _Terminable = exe
            terminable.terminate(handle.terminate_reason)
        handle.events.emit(
            "task_terminated",
            {"reason": handle.terminate_reason},
        )
        with self._lock:
            # Drop from pending if not yet started.
            self._pending = [h for h in self._pending if h.task_id != task_id]
            if handle.status in {"queued", "pending"}:
                handle.status = "terminated"
                self._persist(handle, status="terminated")
        return handle

    def shutdown(self) -> None:
        self._stopped = True
        self._wake.set()
        self._worker.join(timeout=5.0)

    def _worker_loop(self) -> None:
        while not self._stopped:
            handle = self._dequeue()
            if handle is None:
                self._wake.wait(timeout=0.2)
                self._wake.clear()
                continue
            try:
                self._run_handle(handle)
            except Exception as exc:  # noqa: BLE001 — isolate task failures
                logger.warning(
                    "task worker failed",
                    extra={
                        "task_id": handle.task_id,
                        "dataset_id": handle.dataset_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                handle.status = "failed"
                handle.events.emit(
                    "task_failed",
                    {"error": f"{type(exc).__name__}: {exc}"},
                )
            finally:
                handle.events.close()
                with self._lock:
                    if self._current is handle:
                        self._current = None
                self._wake.set()

    def _dequeue(self) -> TaskHandle | None:
        with self._lock:
            if self._current is not None:
                return None
            while self._pending:
                handle = self._pending.pop(0)
                if handle.stop_event.is_set():
                    handle.status = "terminated"
                    continue
                self._current = handle
                return handle
            return None

    def _run_handle(self, handle: TaskHandle) -> None:
        tracer = get_tracer()
        query_clip = clip_query_for_trace(handle.user_query)
        with tracer.trace(
            f"task:{handle.task_id}",
            metadata={
                "task_id": handle.task_id,
                "dataset_id": handle.dataset_id,
                "user_query": query_clip,
            },
            input={"user_query": query_clip},
        ) as root:
            try:
                self._run_handle_body(handle)
                root.update(
                    output={
                        "status": handle.status,
                        "has_final_report": bool(
                            (handle.final_state or {}).get("final_report")
                        ),
                    }
                )
            finally:
                tracer.flush()

    def _run_handle_body(self, handle: TaskHandle) -> None:
        handle.status = "running"
        executor = self._executor_factory()
        handle.executor = executor
        human = NodeHumanConfirmPlan(confirm_gate=self._gate, audit_store=self._audit)
        handle.human_confirm = human
        execute_node = NodeExecute(executor=executor, audit_store=self._audit)
        nodes: MainGraphNodes = default_nodes(
            human_confirm=human,
            execute=execute_node,
        )
        # Share audit store across default nodes where possible.
        for attr in (
            "intent_parse",
            "plan",
            "plan_validate",
            "generate_op",
            "safety_check",
            "result_validate",
            "error_classify",
            "reflect",
            "report",
            "fallback_report",
        ):
            node = getattr(nodes, attr, None)
            if node is not None and hasattr(node, "_audit_store"):
                node._audit_store = self._audit  # noqa: SLF001

        if self._checkpointer_factory is not None:
            checkpointer = self._checkpointer_factory()
        else:
            from langgraph.checkpoint.memory import MemorySaver

            checkpointer = MemorySaver()

        app = build_main_graph(nodes=nodes, checkpointer=checkpointer)
        handle.graph = app
        config = make_thread_config(handle.task_id)

        handle.events.emit("task_started", {"dataset_id": handle.dataset_id})
        self._persist(handle, status="running")

        if handle.stop_event.is_set():
            self._finish_terminated(handle)
            return

        for chunk in app.stream(
            dict(handle.initial_state), config, stream_mode="updates"
        ):
            if handle.stop_event.is_set():
                self._finish_terminated(handle)
                return
            self._emit_node_updates(handle, chunk)

        snap = app.get_state(config)
        values = dict(snap.values) if snap and snap.values else {}
        handle.final_state = values

        if values.get("status") == "waiting_confirm" and not handle.stop_event.is_set():
            handle.status = "waiting_confirm"
            handle.events.emit("waiting_confirm", {"node": "human_confirm"})
            self._persist(handle, status="waiting_confirm", values=values)
            while not handle.confirm_event.wait(timeout=0.2):
                if handle.stop_event.is_set():
                    self._finish_terminated(handle)
                    return
            if handle.stop_event.is_set():
                self._finish_terminated(handle)
                return
            record = handle.confirm_record or self._gate.resolve(handle.task_id)
            decision = record.decision if record is not None else "reject"
            next_node = "generate_op" if decision == "accept" else "report"
            app.update_state(
                config,
                {"status": "running", "next_node": next_node},
                as_node="human_confirm",
            )
            for chunk in app.stream(None, config, stream_mode="updates"):
                if handle.stop_event.is_set():
                    self._finish_terminated(handle)
                    return
                self._emit_node_updates(handle, chunk)
            snap = app.get_state(config)
            values = dict(snap.values) if snap and snap.values else values
            handle.final_state = values

        if handle.stop_event.is_set():
            self._finish_terminated(handle)
            return

        status = str(values.get("status") or "success")
        if status not in {"success", "failed", "fallback", "terminated"}:
            status = "success" if values.get("final_report") else "failed"
        handle.status = status
        handle.events.emit(
            "task_completed",
            {
                "status": status,
                "has_final_report": values.get("final_report") is not None,
                "has_fallback_report": values.get("fallback_report") is not None,
            },
        )
        self._persist(handle, status=status, values=values)

    def _emit_node_updates(self, handle: TaskHandle, chunk: Any) -> None:
        if not isinstance(chunk, dict):
            return
        for node_name in chunk:
            handle.events.emit("node_entered", {"node": str(node_name)})

    def _finish_terminated(self, handle: TaskHandle) -> None:
        handle.status = "terminated"
        values = dict(handle.final_state or {})
        values["status"] = "terminated"
        # Preserve completed sub_task_results — never clear.
        handle.final_state = values
        handle.events.emit(
            "task_completed",
            {
                "status": "terminated",
                "reason": handle.terminate_reason or "terminated",
            },
        )
        self._persist(handle, status="terminated", values=values)

    def _persist(
        self,
        handle: TaskHandle,
        *,
        status: str,
        values: dict[str, Any] | None = None,
    ) -> None:
        if self._audit is None:
            return
        merged: dict[str, Any] = dict(handle.initial_state)
        if values:
            merged.update(values)
        merged["status"] = status
        merged["task_id"] = handle.task_id
        merged["dataset_id"] = handle.dataset_id
        try:
            self._audit.save_task(cast(TaskState, merged))
        except Exception:  # noqa: BLE001
            logger.warning(
                "persist task failed",
                extra={"task_id": handle.task_id, "dataset_id": handle.dataset_id},
            )
