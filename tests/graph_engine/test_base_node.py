# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest

from data_reflex.graph_engine.nodes.base import BaseNode, truncate_error_text
from data_reflex.models.state import TaskState, create_initial_task_state
from data_reflex.modules.audit.store import AuditStore
from data_reflex.utils.tracing import create_tracer, reset_tracer


class _CountingNode(BaseNode):
    name: ClassVar[str] = "Node_Counting"

    def __init__(self, *, fail_times: int = 0, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fail_times = fail_times
        self.calls = 0

    def execute(self, state: TaskState) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"boom-attempt-{self.calls}")
        return {"llm_calls_used": int(state.get("llm_calls_used") or 0) + 1}


class _AlwaysFailNode(BaseNode):
    name: ClassVar[str] = "Node_AlwaysFail"

    def execute(self, state: TaskState) -> dict[str, Any]:
        _ = state
        raise ValueError("email=alice@example.com phone=13800138000 amount=1234567890")


@pytest.fixture()
def store(tmp_path: Path) -> AuditStore:
    return AuditStore(tmp_path / "audit.db")


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


def _seed(store: AuditStore, **kwargs: Any) -> TaskState:
    state = create_initial_task_state(
        dataset_id="ds-1",
        user_query="渠道对比",
        task_id="task-node-1",
        **kwargs,
    )
    store.save_task(state)
    return state


def test_success_returns_delta_and_audits_duration(store: AuditStore) -> None:
    state = _seed(store)
    node = _CountingNode(fail_times=0, audit_store=store)
    delta = node.run(state)
    assert delta["llm_calls_used"] == 1
    assert delta["status"] == "running"  # pending → running
    assert node.calls == 1

    events = store.list_audit_events("task-node-1")
    types = [e.event_type for e in events]
    assert "node_started" in types
    assert "node_completed" in types
    completed = next(e for e in events if e.event_type == "node_completed")
    assert completed.payload["node"] == "Node_Counting"
    assert "duration_ms" in completed.payload
    assert completed.payload["duration_ms"] >= 0


def test_first_failure_retries_once_then_succeeds(store: AuditStore) -> None:
    state = _seed(store)
    node = _CountingNode(fail_times=1, audit_store=store)
    delta = node.run(state)
    assert node.calls == 2
    assert delta["llm_calls_used"] == 1

    events = store.list_audit_events("task-node-1")
    types = [e.event_type for e in events]
    assert "node_retry" in types
    assert "node_completed" in types
    assert "node_failed" not in types


def test_double_failure_writes_failed_status_and_reraises(store: AuditStore) -> None:
    state = _seed(store)
    node = _AlwaysFailNode(audit_store=store)
    with pytest.raises(ValueError, match="alice@example.com"):
        node.run(state)

    loaded = store.load_task("task-node-1")
    assert loaded is not None
    assert loaded["status"] == "failed"

    events = store.list_audit_events("task-node-1")
    types = [e.event_type for e in events]
    assert types.count("node_retry") == 1
    assert "node_failed" in types
    failed = next(e for e in events if e.event_type == "node_failed")
    assert failed.payload["node"] == "Node_AlwaysFail"
    assert "duration_ms" in failed.payload
    # Error text in audit must be truncated / scrubbed (no raw email/phone).
    summary = str(failed.payload.get("error_summary", ""))
    assert "alice@example.com" not in summary
    assert "13800138000" not in summary
    assert "1234567890" not in summary
    assert "REDACTED" in summary


def test_truncate_error_text_helper() -> None:
    raw = 'bad row value="极长的用户地址明细内容ABCDEFG123456" phone=13900001111'
    out = truncate_error_text(raw, max_len=120)
    assert "13900001111" not in out
    assert len(out) <= 120


def test_audit_write_failure_does_not_break_success(tmp_path: Path) -> None:
    class _BrokenStore:
        def append_audit_event(self, *args: Any, **kwargs: Any) -> int:
            raise RuntimeError("disk full")

        def save_task(self, state: TaskState) -> None:
            raise RuntimeError("disk full")

    state = create_initial_task_state(
        dataset_id="ds-x", user_query="q", task_id="task-x"
    )
    node = _CountingNode(fail_times=0, audit_store=_BrokenStore())  # type: ignore[arg-type]
    delta = node.run(state)
    assert delta["llm_calls_used"] == 1
