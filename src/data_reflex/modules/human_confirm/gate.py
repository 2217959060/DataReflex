# -*- coding: utf-8 -*-
"""计划确认门：幂等提交；超时视为拒绝。

上游：``Node_HumanConfirmPlan`` / API confirm。
本文件：按 task 记录等待窗与最终决定（accept/reject/timeout）。
下游：节点据此写 ``waiting_confirm`` 或 ``next_node``。

同一 idempotency_key 重复提交不二次变更（``mutated=False``）。

See ``tasks.md`` T23 / D-CONFIRM-01。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from data_reflex.models.state import ConfirmReq
from data_reflex.utils.logger import get_logger

logger = get_logger(__name__)

ConfirmDecision = Literal["accept", "reject", "timeout"]


@dataclass(frozen=True)
class ConfirmRecord:
    """某任务的最终确认结果。"""

    task_id: str
    decision: ConfirmDecision
    idempotency_key: str | None
    mutated: bool
    """仅当本次调用首次写入决定时为 True。"""


class PlanConfirmGate:
    """维护每任务等待窗口与幂等确认；超时按 reject 处理。"""

    def __init__(
        self,
        *,
        timeout_seconds: int = 300,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be >= 0")
        self._timeout_seconds = timeout_seconds
        self._time = time_fn or time.monotonic
        self._wait_started: dict[str, float] = {}
        self._resolved: dict[str, ConfirmRecord] = {}
        self._by_key: dict[str, ConfirmRecord] = {}
        self._mutation_counts: dict[str, int] = {}

    @property
    def timeout_seconds(self) -> int:
        return self._timeout_seconds

    def mutation_count(self, task_id: str) -> int:
        """How many times the resolved decision for ``task_id`` was written."""
        return self._mutation_counts.get(task_id, 0)

    def ensure_waiting(self, task_id: str) -> None:
        """Start the confirm timeout window if not already started/resolved."""
        if task_id in self._resolved:
            return
        if task_id not in self._wait_started:
            self._wait_started[task_id] = self._time()
            logger.info(
                "plan confirm wait started",
                extra={
                    "task_id": task_id,
                    "dataset_id": "-",
                    "timeout_seconds": self._timeout_seconds,
                },
            )

    def submit(self, req: ConfirmReq) -> ConfirmRecord:
        """Apply accept/reject. Same ``idempotency_key`` is a no-op replay."""
        if req.confirm_type != "plan":
            # v1 human-confirm node only handles plan confirms.
            raise ValueError(f"unsupported confirm_type: {req.confirm_type}")

        existing_key = self._by_key.get(req.idempotency_key)
        if existing_key is not None:
            return ConfirmRecord(
                task_id=existing_key.task_id,
                decision=existing_key.decision,
                idempotency_key=existing_key.idempotency_key,
                mutated=False,
            )

        if req.task_id in self._resolved:
            # Already decided (possibly via timeout); do not flip decision.
            prev = self._resolved[req.task_id]
            # Remember key mapping for future replays of this new key → same outcome.
            self._by_key[req.idempotency_key] = prev
            return ConfirmRecord(
                task_id=prev.task_id,
                decision=prev.decision,
                idempotency_key=req.idempotency_key,
                mutated=False,
            )

        self.ensure_waiting(req.task_id)
        record = ConfirmRecord(
            task_id=req.task_id,
            decision=req.decision,
            idempotency_key=req.idempotency_key,
            mutated=True,
        )
        self._resolved[req.task_id] = record
        self._by_key[req.idempotency_key] = record
        self._mutation_counts[req.task_id] = (
            self._mutation_counts.get(req.task_id, 0) + 1
        )
        logger.info(
            "plan confirm submitted",
            extra={
                "task_id": req.task_id,
                "dataset_id": "-",
                "decision": req.decision,
            },
        )
        return record

    def resolve(self, task_id: str) -> ConfirmRecord | None:
        """Return decision if known; auto-timeout when wait window elapsed.

        Returns ``None`` while still waiting within the timeout.
        """
        if task_id in self._resolved:
            prev = self._resolved[task_id]
            return ConfirmRecord(
                task_id=prev.task_id,
                decision=prev.decision,
                idempotency_key=prev.idempotency_key,
                mutated=False,
            )

        started = self._wait_started.get(task_id)
        if started is None:
            return None

        elapsed = self._time() - started
        if elapsed >= self._timeout_seconds:
            record = ConfirmRecord(
                task_id=task_id,
                decision="timeout",
                idempotency_key=None,
                mutated=True,
            )
            self._resolved[task_id] = record
            self._mutation_counts[task_id] = self._mutation_counts.get(task_id, 0) + 1
            logger.info(
                "plan confirm timed out",
                extra={"task_id": task_id, "dataset_id": "-"},
            )
            return record
        return None
