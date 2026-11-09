# -*- coding: utf-8 -*-
"""T36: end-to-end workflow integration (Mock LLM + Mock Sandbox)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from data_reflex.api.app import create_app
from data_reflex.config.settings import Settings
from data_reflex.graph_engine.builder import (
    build_main_graph,
    default_nodes,
    make_thread_config,
    open_sqlite_checkpointer,
)
from data_reflex.graph_engine.nodes.execute import NodeExecute
from data_reflex.models.schemas import ExecuteReq, ExecuteResp
from data_reflex.models.state import StrategyFlags, create_initial_task_state
from data_reflex.modules.audit.store import AuditStore
from data_reflex.modules.executor.mock_executor import MockSandboxExecutor, MockScenario
from data_reflex.utils.tracing import create_tracer, reset_tracer

FIXTURES = Path(__file__).resolve().parents[2] / "eval" / "datasets" / "fixtures"
_QUERY = "对比 APP 和官网 渠道销售额"


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


def _seed(
    *,
    task_id: str,
    query: str = _QUERY,
    flags: StrategyFlags | None = None,
    model_unavailable: bool = False,
) -> dict[str, Any]:
    state: dict[str, Any] = dict(
        create_initial_task_state(
            dataset_id="ds-int",
            user_query=query,
            task_id=task_id,
            strategy_flags=flags
            or StrategyFlags(
                enable_human_confirm=False,
                enable_fallback_report=True,
                enable_reflector=True,
            ),
        )
    )
    if model_unavailable:
        state["model_unavailable"] = True
    return state


def _attach_audit(nodes: Any, audit: AuditStore) -> Any:
    for attr in (
        "intent_parse",
        "plan",
        "plan_validate",
        "human_confirm",
        "generate_op",
        "safety_check",
        "execute",
        "result_validate",
        "error_classify",
        "reflect",
        "report",
        "fallback_report",
    ):
        node = getattr(nodes, attr, None)
        if node is not None and hasattr(node, "_audit_store"):
            node._audit_store = audit  # noqa: SLF001
    return nodes


def _assert_audit_complete(audit: AuditStore, task_id: str) -> list[Any]:
    events = audit.list_audit_events(task_id)
    assert events, "expected audit events"
    types = {e.event_type for e in events}
    assert "node_started" in types
    assert "node_completed" in types
    # Sequence by created_at,id must be unique ids ascending.
    ids = [e.id for e in events]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    return events


class _FlakyThenOkExecutor:
    def __init__(self) -> None:
        self.calls = 0
        self._fail = MockSandboxExecutor(MockScenario.SYNTAX_ERROR)
        self._ok = MockSandboxExecutor(MockScenario.SUCCESS)

    def execute(self, req: ExecuteReq) -> ExecuteResp:
        self.calls += 1
        if self.calls == 1:
            return self._fail.execute(req)
        return self._ok.execute(req)


class _OkThenResourceKillExecutor:
    """First subtask succeeds; second hits resource_killed → partial success."""

    def __init__(self) -> None:
        self.calls = 0
        self._ok = MockSandboxExecutor(MockScenario.SUCCESS)
        self._kill = MockSandboxExecutor(MockScenario.RESOURCE_LIMIT)

    def execute(self, req: ExecuteReq) -> ExecuteResp:
        self.calls += 1
        if self.calls == 1:
            return self._ok.execute(req)
        return self._kill.execute(req)


class _BlockingExecutor:
    def __init__(self) -> None:
        self._gate = threading.Event()
        self._terminated = False
        self.calls = 0
        self._ok = MockSandboxExecutor(MockScenario.SUCCESS)

    @property
    def terminated(self) -> bool:
        return self._terminated

    def terminate(self, reason: str = "") -> None:
        self._terminated = True
        self._gate.set()

    def release(self) -> None:
        self._gate.set()

    def execute(self, req: ExecuteReq) -> ExecuteResp:
        self.calls += 1
        self._gate.wait(timeout=30)
        if self._terminated:
            return ExecuteResp(
                success=False,
                stderr="sandbox terminated",
                exit_code=137,
            )
        return self._ok.execute(req)


# ---------------------------------------------------------------------------
# Graph paths (1–4)
# ---------------------------------------------------------------------------


def test_path_full_success_final_report_and_audit(tmp_path: Path) -> None:
    audit = AuditStore(tmp_path / "ok.db")
    try:
        nodes = _attach_audit(
            default_nodes(
                execute=NodeExecute(
                    executor=MockSandboxExecutor(MockScenario.SUCCESS),
                    audit_store=audit,
                )
            ),
            audit,
        )
        with open_sqlite_checkpointer(":memory:") as cp:
            app = build_main_graph(nodes=nodes, checkpointer=cp)
            out = app.invoke(
                _seed(task_id="int-ok"),
                config=make_thread_config("int-ok"),
            )
        assert out.get("final_report") is not None
        assert out["final_report"].summary
        assert out.get("fallback_report") is None
        assert any(r.status == "success" for r in out.get("sub_task_results") or [])
        _assert_audit_complete(audit, "int-ok")
    finally:
        audit.close()


def test_path_fail_heal_success_final_report_and_audit(tmp_path: Path) -> None:
    audit = AuditStore(tmp_path / "heal.db")
    flaky = _FlakyThenOkExecutor()
    try:
        nodes = _attach_audit(
            default_nodes(execute=NodeExecute(executor=flaky, audit_store=audit)),
            audit,
        )
        with open_sqlite_checkpointer(":memory:") as cp:
            app = build_main_graph(nodes=nodes, checkpointer=cp)
            out = app.invoke(
                _seed(task_id="int-heal"),
                config=make_thread_config("int-heal"),
            )
        assert flaky.calls >= 2
        assert out.get("final_report") is not None
        assert any(r.status == "success" for r in out.get("sub_task_results") or [])
        _assert_audit_complete(audit, "int-heal")
    finally:
        audit.close()


def test_path_partial_success_final_report_and_audit(tmp_path: Path) -> None:
    audit = AuditStore(tmp_path / "partial.db")
    mixed = _OkThenResourceKillExecutor()
    try:
        nodes = _attach_audit(
            default_nodes(execute=NodeExecute(executor=mixed, audit_store=audit)),
            audit,
        )
        with open_sqlite_checkpointer(":memory:") as cp:
            app = build_main_graph(nodes=nodes, checkpointer=cp)
            out = app.invoke(
                _seed(task_id="int-partial"),
                config=make_thread_config("int-partial"),
            )
        report = out.get("final_report")
        assert report is not None
        results = list(out.get("sub_task_results") or [])
        assert any(r.status == "success" for r in results)
        assert any(r.status == "failed" for r in results)
        assert report.partial_success is True
        _assert_audit_complete(audit, "int-partial")
    finally:
        audit.close()


def test_path_fallback_report_and_audit(tmp_path: Path) -> None:
    audit = AuditStore(tmp_path / "fb.db")
    try:
        nodes = _attach_audit(
            default_nodes(
                execute=NodeExecute(
                    executor=MockSandboxExecutor(MockScenario.SUCCESS),
                    audit_store=audit,
                )
            ),
            audit,
        )
        with open_sqlite_checkpointer(":memory:") as cp:
            app = build_main_graph(nodes=nodes, checkpointer=cp)
            out = app.invoke(
                _seed(
                    task_id="int-fb",
                    query="请帮我训练一个神经网络预测房价",
                    model_unavailable=True,
                ),
                config=make_thread_config("int-fb"),
            )
        assert out.get("fallback_report") is not None
        assert out["fallback_report"].is_analysis_conclusion is False
        assert out.get("status") == "fallback"
        _assert_audit_complete(audit, "int-fb")
    finally:
        audit.close()


# ---------------------------------------------------------------------------
# API: serial queue + SSE reconnect (paths 5–6)
# ---------------------------------------------------------------------------


def _import_fixtures(client: TestClient) -> str:
    files = [
        ("files", ("orders.csv", (FIXTURES / "orders.csv").read_bytes(), "text/csv")),
        ("files", ("returns.csv", (FIXTURES / "returns.csv").read_bytes(), "text/csv")),
        ("files", ("users.csv", (FIXTURES / "users.csv").read_bytes(), "text/csv")),
    ]
    resp = client.post("/api/datasets/import", files=files)
    assert resp.status_code == 200, resp.text
    return str(resp.json()["dataset_id"])


def _wait_trace(
    client: TestClient, task_id: str, *statuses: str, timeout: float = 15.0
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        tr = client.get(f"/api/tasks/{task_id}/trace")
        assert tr.status_code == 200
        last = tr.json()
        if last.get("status") in statuses:
            return last
        time.sleep(0.05)
    raise AssertionError(f"status not in {statuses}: last={last}")


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in raw.splitlines():
        if not line.strip():
            if current:
                events.append(current)
                current = {}
            continue
        if line.startswith("id:"):
            current["id"] = line[3:].strip()
        elif line.startswith("event:"):
            current["event"] = line[6:].strip()
        elif line.startswith("data:"):
            current["data"] = json.loads(line[5:].strip())
    if current:
        events.append(current)
    return events


def test_path_serial_queue_both_produce_reports(tmp_path: Path) -> None:
    settings = Settings(sqlite_path=str(tmp_path / "serial.db"))
    blockers: list[_BlockingExecutor] = []
    lock = threading.Lock()

    def _factory() -> _BlockingExecutor:
        exe = _BlockingExecutor()
        with lock:
            blockers.append(exe)
        return exe

    application = create_app(
        settings=settings,
        dataset_root=tmp_path / "datasets",
        sqlite_path=tmp_path / "serial.db",
        upload_dir=tmp_path / "uploads",
        executor_factory=_factory,
    )
    client = TestClient(application)
    try:
        dataset_id = _import_fixtures(client)
        flags = {"enable_human_confirm": False}
        r1 = client.post(
            "/api/tasks",
            json={
                "dataset_id": dataset_id,
                "user_query": _QUERY,
                "strategy_flags": flags,
            },
        )
        r2 = client.post(
            "/api/tasks",
            json={
                "dataset_id": dataset_id,
                "user_query": "各渠道销售额对比",
                "strategy_flags": flags,
            },
        )
        assert r1.json()["queue_position"] == 0
        assert r2.json()["queue_position"] >= 1
        t1, t2 = r1.json()["task_id"], r2.json()["task_id"]

        deadline = time.time() + 10
        while time.time() < deadline and len(blockers) < 1:
            time.sleep(0.05)
        assert len(blockers) == 1
        blockers[0].release()
        tr1 = _wait_trace(client, t1, "success", "failed", "fallback")
        assert tr1.get("final_report") or tr1.get("fallback_report")

        deadline = time.time() + 10
        while time.time() < deadline and len(blockers) < 2:
            time.sleep(0.05)
        assert len(blockers) == 2
        blockers[1].release()
        tr2 = _wait_trace(client, t2, "success", "failed", "fallback")
        assert tr2.get("final_report") or tr2.get("fallback_report")

        # Audit complete for both tasks.
        audit = application.state.runtime.audit_store
        _assert_audit_complete(audit, t1)
        _assert_audit_complete(audit, t2)
    finally:
        for b in blockers:
            b.release()
        application.state.runtime.queue.shutdown()
        application.state.runtime.audit_store.close()


def test_sse_reconnect_seq_continuous_no_duplicates(tmp_path: Path) -> None:
    settings = Settings(sqlite_path=str(tmp_path / "sse.db"))
    application = create_app(
        settings=settings,
        dataset_root=tmp_path / "datasets",
        sqlite_path=tmp_path / "sse.db",
        upload_dir=tmp_path / "uploads",
        executor_factory=lambda: MockSandboxExecutor(MockScenario.SUCCESS),
    )
    client = TestClient(application)
    try:
        dataset_id = _import_fixtures(client)
        submit = client.post(
            "/api/tasks",
            json={
                "dataset_id": dataset_id,
                "user_query": _QUERY,
                "strategy_flags": {"enable_human_confirm": False},
            },
        )
        task_id = submit.json()["task_id"]
        _wait_trace(client, task_id, "success", "failed", "fallback")

        with client.stream("GET", f"/api/tasks/{task_id}/stream") as stream:
            all_events = _parse_sse("".join(stream.iter_text()))
        assert len(all_events) >= 3
        seqs = [int(e["id"]) for e in all_events]
        assert seqs == list(range(len(seqs))), "seq must be contiguous from 0"
        assert len(seqs) == len(set(seqs)), "seq must be unique"

        types = [e["event"] for e in all_events]
        assert "task_started" in types
        assert "node_entered" in types
        assert "task_completed" in types

        cut = all_events[1]["data"]["seq"]
        with client.stream(
            "GET", f"/api/tasks/{task_id}/stream", params={"after_seq": cut}
        ) as stream:
            replay = _parse_sse("".join(stream.iter_text()))
        replay_seqs = [e["data"]["seq"] for e in replay]
        expected = [e["data"]["seq"] for e in all_events if e["data"]["seq"] > cut]
        assert replay_seqs == expected
        assert all(s > cut for s in replay_seqs)

        # Report present + audit.
        trace = client.get(f"/api/tasks/{task_id}/trace").json()
        assert trace.get("final_report") or trace.get("fallback_report")
        _assert_audit_complete(application.state.runtime.audit_store, task_id)
    finally:
        application.state.runtime.queue.shutdown()
        application.state.runtime.audit_store.close()
