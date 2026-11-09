# -*- coding: utf-8 -*-
"""SQLite 持久化：任务状态、checkpoint、审计事件、评测结果。

上游：Queue / BaseNode / 评测脚本写读。
本文件：标准库 sqlite3 + 进程内 RLock 串行写（WAL）；payload 经脱敏。
下游：API trace、任务恢复、eval 报表。

表：``tasks`` / ``checkpoints`` / ``audit_events`` / ``eval_cases`` / ``eval_results``。

See ``tasks.md`` T09 / D-STORE-01。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from data_reflex.models.schemas import (
    FallbackReportResp,
    IntentParseResp,
    PlanResp,
    ReportResp,
    SubTaskResult,
)
from data_reflex.models.state import StrategyFlags, TaskState
from data_reflex.utils.logger import get_logger, sanitize_extra

logger = get_logger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    status TEXT NOT NULL,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    checkpoint_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_task
    ON checkpoints (task_id, created_at);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_task_time
    ON audit_events (task_id, created_at, id);

CREATE TABLE IF NOT EXISTS eval_cases (
    case_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    case_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT,
    run_id TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_eval_results_run
    ON eval_results (run_id, created_at, id);
"""


def _utc_now_iso() -> str:
    """UTC timestamp with microseconds for stable ordering."""
    return datetime.now(UTC).isoformat()


def _json_default(obj: object) -> object:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def serialize_task_state(state: TaskState) -> str:
    """Serialize ``TaskState`` (including nested Pydantic models) to JSON."""
    return json.dumps(dict(state), default=_json_default, ensure_ascii=False)


def deserialize_task_state(raw: str) -> TaskState:
    """Rehydrate ``TaskState`` from JSON produced by ``serialize_task_state``."""
    data = json.loads(raw)
    intent_raw = data.get("intent")
    plan_raw = data.get("plan")
    final_raw = data.get("final_report")
    fallback_raw = data.get("fallback_report")
    flags_raw = data.get("strategy_flags") or {}
    results_raw = data.get("sub_task_results") or []

    return TaskState(
        task_id=str(data["task_id"]),
        dataset_id=str(data["dataset_id"]),
        user_query=str(data["user_query"]),
        conversation_context=list(data.get("conversation_context") or []),
        intent=IntentParseResp.model_validate(intent_raw) if intent_raw else None,
        plan=PlanResp.model_validate(plan_raw) if plan_raw else None,
        sub_task_results=[SubTaskResult.model_validate(r) for r in results_raw],
        current_sub_task_id=data.get("current_sub_task_id"),
        retry_counts=dict(data.get("retry_counts") or {}),
        error_fingerprints=list(data.get("error_fingerprints") or []),
        llm_calls_used=int(data.get("llm_calls_used") or 0),
        final_report=ReportResp.model_validate(final_raw) if final_raw else None,
        fallback_report=(
            FallbackReportResp.model_validate(fallback_raw) if fallback_raw else None
        ),
        strategy_flags=StrategyFlags.model_validate(flags_raw),
        audit_trace_id=str(data["audit_trace_id"]),
        status=data["status"],  # validated by TypedDict / callers
    )


def sanitize_audit_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Redact forbidden keys to length placeholders (field names kept, values not)."""
    if not payload:
        return {}
    return sanitize_extra(dict(payload))


@dataclass(frozen=True)
class AuditEvent:
    """一条已落库审计（按 created_at、id 排序）。"""

    id: int
    task_id: str
    event_type: str
    payload: dict[str, Any]
    created_at: str


class AuditStore:
    """进程内 SQLite 存储；所有写路径串行化。"""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we use explicit BEGIN
            timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.executescript(_SCHEMA_SQL)
        logger.info(
            "audit store ready",
            extra={"task_id": "-", "dataset_id": str(self._path)},
        )

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> AuditStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # --- tasks -----------------------------------------------------------------

    def save_task(self, state: TaskState) -> None:
        """Upsert a task row from ``TaskState``."""
        now = _utc_now_iso()
        payload = serialize_task_state(state)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    "SELECT created_at FROM tasks WHERE task_id = ?",
                    (state["task_id"],),
                ).fetchone()
                created_at = str(existing["created_at"]) if existing else now
                self._conn.execute(
                    """
                    INSERT INTO tasks (task_id, dataset_id, status, state_json,
                                      created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        dataset_id = excluded.dataset_id,
                        status = excluded.status,
                        state_json = excluded.state_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        state["task_id"],
                        state["dataset_id"],
                        state["status"],
                        payload,
                        created_at,
                        now,
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def load_task(self, task_id: str) -> TaskState | None:
        """Load task state by id, or ``None`` if missing."""
        with self._lock:
            row = self._conn.execute(
                "SELECT state_json FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return deserialize_task_state(str(row["state_json"]))

    # --- checkpoints (table required by T09; used by Graph later) ---------------

    def save_checkpoint(
        self,
        *,
        checkpoint_id: str,
        task_id: str,
        thread_id: str,
        checkpoint: Mapping[str, Any],
    ) -> None:
        """Upsert a LangGraph-oriented checkpoint blob."""
        now = _utc_now_iso()
        blob = json.dumps(dict(checkpoint), default=_json_default, ensure_ascii=False)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    INSERT INTO checkpoints (
                        checkpoint_id, task_id, thread_id, checkpoint_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(checkpoint_id) DO UPDATE SET
                        task_id = excluded.task_id,
                        thread_id = excluded.thread_id,
                        checkpoint_json = excluded.checkpoint_json,
                        created_at = excluded.created_at
                    """,
                    (checkpoint_id, task_id, thread_id, blob, now),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def load_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT checkpoint_json FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
        if row is None:
            return None
        loaded = json.loads(str(row["checkpoint_json"]))
        return dict(loaded) if isinstance(loaded, dict) else {"value": loaded}

    # --- audit events ----------------------------------------------------------

    def append_audit_event(
        self,
        task_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        created_at: str | None = None,
    ) -> int:
        """Append one audit event; returns the new row ``id``."""
        ts = created_at or _utc_now_iso()
        safe_payload = sanitize_audit_payload(payload)
        # Ensure forbidden raw values are not reconstituted via nested dumps.
        blob = json.dumps(safe_payload, ensure_ascii=False, default=_json_default)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(
                    """
                    INSERT INTO audit_events (task_id, event_type, payload_json, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (task_id, event_type, blob, ts),
                )
                row_id = int(cur.lastrowid or 0)
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return row_id

    def list_audit_events(self, task_id: str) -> list[AuditEvent]:
        """Return events for ``task_id`` ordered by time then id (ascending)."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, task_id, event_type, payload_json, created_at
                FROM audit_events
                WHERE task_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (task_id,),
            ).fetchall()
        events: list[AuditEvent] = []
        for row in rows:
            payload_raw = json.loads(str(row["payload_json"]))
            payload = dict(payload_raw) if isinstance(payload_raw, dict) else {}
            events.append(
                AuditEvent(
                    id=int(row["id"]),
                    task_id=str(row["task_id"]),
                    event_type=str(row["event_type"]),
                    payload=payload,
                    created_at=str(row["created_at"]),
                )
            )
        return events

    # --- eval ------------------------------------------------------------------

    def save_eval_case(
        self,
        *,
        case_id: str,
        category: str,
        case: Mapping[str, Any],
    ) -> None:
        """Upsert an eval case definition (table required by T09; used by T40+)."""
        now = _utc_now_iso()
        blob = json.dumps(dict(case), default=_json_default, ensure_ascii=False)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    INSERT INTO eval_cases (case_id, category, case_json, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(case_id) DO UPDATE SET
                        category = excluded.category,
                        case_json = excluded.case_json
                    """,
                    (case_id, category, blob, now),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def save_eval_result(
        self,
        *,
        run_id: str,
        mode: str,
        metrics: Mapping[str, Any],
        case_id: str | None = None,
    ) -> int:
        """Append one eval result row; returns new ``id``."""
        now = _utc_now_iso()
        blob = json.dumps(dict(metrics), default=_json_default, ensure_ascii=False)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(
                    """
                    INSERT INTO eval_results (case_id, run_id, metrics_json, mode, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (case_id, run_id, blob, mode, now),
                )
                row_id = int(cur.lastrowid or 0)
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return row_id

    def list_tables(self) -> set[str]:
        """Helper for tests — names of user tables in this database."""
        with self._lock:
            rows = self._conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """).fetchall()
        return {str(r["name"]) for r in rows}
