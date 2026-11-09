# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from data_reflex.models.state import StrategyFlags, create_initial_task_state
from data_reflex.modules.audit.store import (
    AuditStore,
    deserialize_task_state,
    sanitize_audit_payload,
    serialize_task_state,
)

REQUIRED_TABLES = {
    "tasks",
    "checkpoints",
    "audit_events",
    "eval_cases",
    "eval_results",
}


@pytest.fixture()
def store(tmp_path: Path) -> AuditStore:
    db = tmp_path / "data_reflex.db"
    s = AuditStore(db)
    yield s
    s.close()


def test_creates_required_tables(store: AuditStore) -> None:
    assert store.list_tables() >= REQUIRED_TABLES


def test_save_and_load_task_roundtrip(store: AuditStore) -> None:
    state = create_initial_task_state(
        dataset_id="ds-1",
        user_query="退货率 Top3",
        strategy_flags=StrategyFlags(enable_human_confirm=True),
        task_id="task-1",
        audit_trace_id="trace-1",
    )
    store.save_task(state)
    loaded = store.load_task("task-1")
    assert loaded is not None
    assert loaded["task_id"] == "task-1"
    assert loaded["dataset_id"] == "ds-1"
    assert loaded["status"] == "pending"
    assert loaded["retry_counts"] == {}
    assert loaded["error_fingerprints"] == []
    assert loaded["llm_calls_used"] == 0
    assert loaded["strategy_flags"].enable_human_confirm is True

    state["status"] = "running"
    state["llm_calls_used"] = 2
    store.save_task(state)
    loaded2 = store.load_task("task-1")
    assert loaded2 is not None
    assert loaded2["status"] == "running"
    assert loaded2["llm_calls_used"] == 2


def test_load_missing_task_returns_none(store: AuditStore) -> None:
    assert store.load_task("missing") is None


def test_two_audit_events_ordered_by_time(store: AuditStore) -> None:
    state = create_initial_task_state(
        dataset_id="ds-a",
        user_query="q",
        task_id="task-audit",
    )
    store.save_task(state)

    store.append_audit_event(
        "task-audit",
        "plan_completed",
        {"node": "plan"},
        created_at="2025-10-01T00:00:00.000001+00:00",
    )
    store.append_audit_event(
        "task-audit",
        "execute_started",
        {"node": "execute"},
        created_at="2025-10-01T00:00:00.000002+00:00",
    )

    events = store.list_audit_events("task-audit")
    assert len(events) == 2
    assert events[0].event_type == "plan_completed"
    assert events[1].event_type == "execute_started"
    assert events[0].created_at <= events[1].created_at
    assert events[0].id < events[1].id


def test_audit_payload_redacts_op_content(store: AuditStore) -> None:
    state = create_initial_task_state(
        dataset_id="ds-r",
        user_query="q",
        task_id="task-redact",
    )
    store.save_task(state)
    secret = "SELECT phone FROM users; -- leak"
    store.append_audit_event(
        "task-redact",
        "op_generated",
        {"op_content": secret, "op_type": "sql"},
    )
    events = store.list_audit_events("task-redact")
    assert len(events) == 1
    assert secret not in str(events[0].payload)
    assert events[0].payload["op_type"] == "sql"
    assert str(events[0].payload["op_content"]).startswith("[REDACTED]")


def test_sanitize_audit_payload_unit() -> None:
    safe = sanitize_audit_payload(
        {"task_id": "t", "email": "a@b.com", "op_content": "DROP TABLE x"}
    )
    assert safe["task_id"] == "t"
    assert "DROP" not in str(safe["op_content"])
    assert str(safe["email"]).startswith("[REDACTED]")


def test_checkpoint_roundtrip(store: AuditStore) -> None:
    store.save_checkpoint(
        checkpoint_id="cp-1",
        task_id="task-cp",
        thread_id="thread-1",
        checkpoint={"channel_values": {"status": "running"}, "ts": 1},
    )
    loaded = store.load_checkpoint("cp-1")
    assert loaded is not None
    assert loaded["channel_values"]["status"] == "running"


def test_save_eval_result(store: AuditStore) -> None:
    store.save_eval_case(
        case_id="case-1",
        category="single_table",
        case={"question": "渠道对比", "gold": []},
    )
    row_id = store.save_eval_result(
        run_id="run-1",
        mode="mock",
        case_id="case-1",
        metrics={"first_pass_rate": 0.8},
    )
    assert row_id >= 1


def test_concurrent_writes_no_corruption(store: AuditStore) -> None:
    task_id = "task-concurrent"
    state = create_initial_task_state(
        dataset_id="ds-c",
        user_query="concurrent",
        task_id=task_id,
    )
    store.save_task(state)

    n = 40
    errors: list[BaseException] = []
    barrier = threading.Barrier(n)

    def worker(i: int) -> None:
        try:
            barrier.wait(timeout=10)
            store.append_audit_event(task_id, f"event_{i}", {"i": i})
            snap = store.load_task(task_id)
            assert snap is not None
            snap["llm_calls_used"] = i
            store.save_task(snap)
        except BaseException as exc:  # noqa: BLE001 — collect for assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    events = store.list_audit_events(task_id)
    assert len(events) == n
    # Stable order by created_at, id — all present, unique event types.
    types = [e.event_type for e in events]
    assert len(set(types)) == n
    loaded = store.load_task(task_id)
    assert loaded is not None
    assert loaded["task_id"] == task_id
    assert isinstance(loaded["llm_calls_used"], int)


def test_serialize_deserialize_preserves_core_fields() -> None:
    state = create_initial_task_state(
        dataset_id="ds-s",
        user_query="serialize me",
        task_id="task-s",
        audit_trace_id="trace-s",
    )
    raw = serialize_task_state(state)
    back = deserialize_task_state(raw)
    assert back["task_id"] == "task-s"
    assert back["user_query"] == "serialize me"
    assert back["strategy_flags"].enable_planner is True
