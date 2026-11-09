# -*- coding: utf-8 -*-
"""所有 Graph 节点的基类：计时、审计、最多自动重试 1 次、返回状态增量。

上游：``builder.build_main_graph`` 把子类包成 runnable。
本文件：``run()`` 模板；子类只实现 ``execute()`` 写业务增量（含 ``next_node``）。
下游：LangGraph 合并 delta；条件边读 ``next_node`` 跳转。

节点内异常重试 ≠ Reflect 自愈回环（后者在业务失败后走 error_classify→reflect）。

See ``tasks.md`` T19 / D-NODE-01.
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from data_reflex.models.state import TaskState
from data_reflex.modules.audit.store import AuditStore
from data_reflex.utils.logger import get_logger, log_exception, scrub_message
from data_reflex.utils.tracing import Tracer, get_tracer

logger = get_logger(__name__)

# 节点自身异常：默认共 2 次尝试（首次失败再自动重试 1 次）
NODE_MAX_ATTEMPTS = 2
_ERROR_TEXT_MAX = 240
_LONG_NUMBER_RE = re.compile(r"\d{8,}")
_QUOTED_VALUE_RE = re.compile(r"""(['"])(.{24,}?)\1""")


def truncate_error_text(message: str, *, max_len: int = _ERROR_TEXT_MAX) -> str:
    """Scrub PII-like tokens and truncate for audit / status surfaces."""
    text = scrub_message(str(message))
    text = _LONG_NUMBER_RE.sub("[REDACTED]:num", text)

    def _shorten_quoted(match: re.Match[str]) -> str:
        quote = match.group(1)
        body = match.group(2)
        return f"{quote}[REDACTED]:len={len(body)}{quote}"

    text = _QUOTED_VALUE_RE.sub(_shorten_quoted, text)
    text = " ".join(text.split())
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


class BaseNode(ABC):
    """节点模板：子类 ``execute`` 只返回状态增量；``run`` 负责审计/追踪/异常重试。"""

    name: ClassVar[str] = "BaseNode"

    def __init__(
        self,
        *,
        audit_store: AuditStore | None = None,
        tracer: Tracer | None = None,
        max_attempts: int = NODE_MAX_ATTEMPTS,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._audit_store = audit_store
        self._tracer = tracer
        self._max_attempts = max_attempts

    def __call__(self, state: TaskState) -> dict[str, Any]:
        """LangGraph-friendly entrypoint."""
        return self.run(state)

    def run(self, state: TaskState) -> dict[str, Any]:
        """Execute with retry / audit / tracing; return a state delta."""
        task_id = str(state.get("task_id") or "-")
        dataset_id = str(state.get("dataset_id") or "-")
        started = time.perf_counter()
        self._emit_audit(
            task_id,
            "node_started",
            {"node": self.name, "dataset_id": dataset_id},
        )

        tracer = self._tracer if self._tracer is not None else get_tracer()
        last_exc: BaseException | None = None

        with tracer.span(
            self.name,
            metadata={"task_id": task_id, "dataset_id": dataset_id},
        ) as span:
            for attempt in range(1, self._max_attempts + 1):
                try:
                    delta = self.execute(state)
                    if not isinstance(delta, dict):
                        raise TypeError(
                            f"{self.name}.execute must return a dict state delta"
                        )
                    duration_ms = round((time.perf_counter() - started) * 1000, 3)
                    out = self._finalize_success_delta(state, delta)
                    span.update(output={"ok": True, "attempt": attempt})
                    self._emit_audit(
                        task_id,
                        "node_completed",
                        {
                            "node": self.name,
                            "duration_ms": duration_ms,
                            "attempt": attempt,
                            "status": out.get("status", state.get("status")),
                        },
                    )
                    return out
                except Exception as exc:
                    last_exc = exc
                    duration_so_far = round((time.perf_counter() - started) * 1000, 3)
                    summary = truncate_error_text(f"{type(exc).__name__}: {exc}")
                    log_exception(
                        logger,
                        "node attempt failed",
                        extra={
                            "task_id": task_id,
                            "dataset_id": dataset_id,
                            "node": self.name,
                            "attempt": attempt,
                        },
                        exc=exc,
                    )
                    if attempt < self._max_attempts:
                        self._emit_audit(
                            task_id,
                            "node_retry",
                            {
                                "node": self.name,
                                "attempt": attempt,
                                "duration_ms": duration_so_far,
                                "error_summary": summary,
                            },
                        )
                        continue

                    # Exhausted retries: persist failed status, audit, re-raise.
                    error_delta = self._failure_delta(state, summary)
                    self._persist_failed_state(state, error_delta)
                    self._emit_audit(
                        task_id,
                        "node_failed",
                        {
                            "node": self.name,
                            "duration_ms": duration_so_far,
                            "attempt": attempt,
                            "error_summary": summary,
                            "error_type": type(exc).__name__,
                        },
                    )
                    span.update(
                        output={"ok": False, "error_summary": summary},
                        status_message=summary,
                    )
                    raise

        # Unreachable, but keeps type-checkers happy.
        assert last_exc is not None
        raise last_exc

    @abstractmethod
    def execute(self, state: TaskState) -> dict[str, Any]:
        """Run node logic once; return a partial ``TaskState`` update."""

    def _finalize_success_delta(
        self, state: TaskState, delta: dict[str, Any]
    ) -> dict[str, Any]:
        out = dict(delta)
        if "status" not in out and state.get("status") == "pending":
            out["status"] = "running"
        return out

    def _failure_delta(self, state: TaskState, error_summary: str) -> dict[str, Any]:
        """Minimal error state written on exhausted retries."""
        _ = error_summary  # recorded in audit payload, not TaskState fields
        _ = state
        return {"status": "failed"}

    def _persist_failed_state(
        self, state: TaskState, error_delta: dict[str, Any]
    ) -> None:
        if self._audit_store is None:
            return
        merged: dict[str, Any] = {**dict(state), **error_delta}
        try:
            self._audit_store.save_task(merged)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 — audit must not block
            logger.warning(
                "failed to persist node error state",
                extra={
                    "task_id": str(state.get("task_id") or "-"),
                    "dataset_id": str(state.get("dataset_id") or "-"),
                    "error": type(exc).__name__,
                },
            )

    def _emit_audit(
        self, task_id: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        if self._audit_store is None:
            return
        try:
            self._audit_store.append_audit_event(task_id, event_type, payload)
        except Exception as exc:  # noqa: BLE001 — Node_Audit: warn, don't block
            logger.warning(
                "audit event write failed",
                extra={
                    "task_id": task_id,
                    "dataset_id": "-",
                    "event_type": event_type,
                    "error": type(exc).__name__,
                },
            )
