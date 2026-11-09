# -*- coding: utf-8 -*-
"""每任务只追加的 SSE 事件日志：带 seq，支持断线按 after_seq 重放。

上游：``TaskQueue`` / 节点进度 emit。
本文件：线程安全缓冲；订阅方阻塞等待新事件。
下游：``/api/tasks/{id}/events`` SSE 推给前端。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from data_reflex.models.state import TaskEvent


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class TaskEventLog:
    """线程安全事件缓冲；订阅者等待 seq > after_seq 的新事件。"""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self._lock = threading.RLock()
        self._events: list[TaskEvent] = []
        self._cv = threading.Condition(self._lock)
        self._closed = False

    @property
    def next_seq(self) -> int:
        with self._lock:
            return len(self._events)

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> TaskEvent:
        with self._cv:
            ev = TaskEvent(
                task_id=self.task_id,
                seq=len(self._events),
                event_type=event_type,
                payload=dict(payload or {}),
                timestamp=_utc_now_iso(),
            )
            self._events.append(ev)
            self._cv.notify_all()
            return ev

    def snapshot(self, *, after_seq: int = -1) -> list[TaskEvent]:
        with self._lock:
            return [e for e in self._events if e.seq > after_seq]

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()

    def wait_after(
        self, after_seq: int, *, timeout: float | None = None
    ) -> list[TaskEvent]:
        """Block until new events appear or the log is closed."""
        with self._cv:
            if not self._cv.wait_for(
                lambda: any(e.seq > after_seq for e in self._events) or self._closed,
                timeout=timeout,
            ):
                return []
            return [e for e in self._events if e.seq > after_seq]

    def iter_live(
        self, *, after_seq: int = -1, poll_timeout: float = 0.5
    ) -> Iterator[TaskEvent]:
        """Yield historical then live events until the log is closed and drained."""
        cursor = after_seq
        while True:
            batch = self.snapshot(after_seq=cursor)
            if batch:
                for ev in batch:
                    yield ev
                    cursor = ev.seq
                continue
            with self._lock:
                closed = self._closed
            if closed:
                return
            batch = self.wait_after(cursor, timeout=poll_timeout)
            if not batch:
                with self._lock:
                    if self._closed:
                        return
                continue
            for ev in batch:
                yield ev
                cursor = ev.seq
