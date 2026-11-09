# -*- coding: utf-8 -*-
"""T34 FastAPI acceptance: import, queue, SSE, confirm, terminate, trace."""

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
from data_reflex.models.schemas import ExecuteReq, ExecuteResp
from data_reflex.modules.executor.mock_executor import MockSandboxExecutor, MockScenario
from data_reflex.utils.tracing import create_tracer, reset_tracer

FIXTURES = Path(__file__).resolve().parents[2] / "eval" / "datasets" / "fixtures"
_QUERY = "对比 APP 和官网 渠道销售额"


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


class _BlockingExecutor:
    """Blocks in execute until terminate() or release()."""

    def __init__(self) -> None:
        self._gate = threading.Event()
        self._terminated = False
        self._terminate_reason: str | None = None
        self.calls = 0
        self._ok = MockSandboxExecutor(MockScenario.SUCCESS)

    @property
    def terminated(self) -> bool:
        return self._terminated

    def terminate(self, reason: str = "") -> None:
        self._terminated = True
        self._terminate_reason = reason or "terminated"
        self._gate.set()

    def release(self) -> None:
        self._gate.set()

    def execute(self, req: ExecuteReq) -> ExecuteResp:
        self.calls += 1
        self._gate.wait(timeout=30)
        if self._terminated:
            return ExecuteResp(
                success=False,
                stderr=f"sandbox terminated: {self._terminate_reason}",
                exit_code=137,
            )
        return self._ok.execute(req)


@pytest.fixture()
def app_env(tmp_path: Path) -> Any:
    settings = Settings(
        enable_human_confirm=False,
        sqlite_path=str(tmp_path / "api.db"),
        max_upload_files=5,
        max_file_size_mb=5,
    )
    executors: list[Any] = []

    def _factory() -> Any:
        exe = MockSandboxExecutor(MockScenario.SUCCESS)
        executors.append(exe)
        return exe

    application = create_app(
        settings=settings,
        dataset_root=tmp_path / "datasets",
        sqlite_path=tmp_path / "api.db",
        upload_dir=tmp_path / "uploads",
        executor_factory=_factory,
    )
    client = TestClient(application)
    yield {
        "client": client,
        "app": application,
        "executors": executors,
        "tmp_path": tmp_path,
        "settings": settings,
        "factory": _factory,
    }
    application.state.runtime.queue.shutdown()
    application.state.runtime.audit_store.close()


def test_health_exposes_sandbox_backend(app_env: Any) -> None:
    client: TestClient = app_env["client"]
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["sandbox_backend"] in {"mock", "docker"}
    # Default Settings in this fixture leave sandbox_backend at its Settings default.
    assert body["sandbox_backend"] == app_env["settings"].sandbox_backend
    assert "tracing_enabled" in body
    assert body["tracing_enabled"] is False


def test_health_reports_docker_when_configured(tmp_path: Path) -> None:
    settings = Settings(
        enable_human_confirm=False,
        sqlite_path=str(tmp_path / "api-docker.db"),
        sandbox_backend="docker",
    )
    application = create_app(
        settings=settings,
        dataset_root=tmp_path / "datasets",
        sqlite_path=tmp_path / "api-docker.db",
        upload_dir=tmp_path / "uploads",
        executor_factory=lambda: MockSandboxExecutor(MockScenario.SUCCESS),
    )
    client = TestClient(application)
    try:
        body = client.get("/health").json()
        assert body["sandbox_backend"] == "docker"
        assert body["tracing_enabled"] is False
    finally:
        application.state.runtime.queue.shutdown()
        application.state.runtime.audit_store.close()


def test_health_tracing_enabled_when_langfuse_keys_set(tmp_path: Path) -> None:
    settings = Settings(
        enable_human_confirm=False,
        sqlite_path=str(tmp_path / "api-lf.db"),
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",
    )
    application = create_app(
        settings=settings,
        dataset_root=tmp_path / "datasets",
        sqlite_path=tmp_path / "api-lf.db",
        upload_dir=tmp_path / "uploads",
        executor_factory=lambda: MockSandboxExecutor(MockScenario.SUCCESS),
    )
    client = TestClient(application)
    try:
        body = client.get("/health").json()
        assert body["tracing_enabled"] is True
    finally:
        application.state.runtime.queue.shutdown()
        application.state.runtime.audit_store.close()


def _import_fixtures(client: TestClient) -> str:
    files = [
        ("files", ("orders.csv", (FIXTURES / "orders.csv").read_bytes(), "text/csv")),
        ("files", ("returns.csv", (FIXTURES / "returns.csv").read_bytes(), "text/csv")),
        ("files", ("users.csv", (FIXTURES / "users.csv").read_bytes(), "text/csv")),
    ]
    resp = client.post("/api/datasets/import", files=files)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["dataset_id"]
    return str(body["dataset_id"])


def _wait_status(
    client: TestClient, task_id: str, *statuses: str, timeout: float = 10.0
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
            data = line[5:].strip()
            current["data"] = json.loads(data)
    if current:
        events.append(current)
    return events


def test_import_csv_returns_dataset_id(app_env: dict[str, Any]) -> None:
    client: TestClient = app_env["client"]
    dataset_id = _import_fixtures(client)
    assert len(dataset_id) > 8


def test_sse_task_started_node_entered_completed_seq(app_env: dict[str, Any]) -> None:
    client: TestClient = app_env["client"]
    dataset_id = _import_fixtures(client)
    submit = client.post(
        "/api/tasks",
        json={
            "dataset_id": dataset_id,
            "user_query": _QUERY,
            "strategy_flags": {
                "enable_human_confirm": False,
                "enable_planner": True,
                "enable_reflector": True,
                "enable_sandbox": True,
                "enable_fallback_report": True,
            },
        },
    )
    assert submit.status_code == 200
    task_id = submit.json()["task_id"]
    _wait_status(client, task_id, "success", "failed", "fallback")

    with client.stream("GET", f"/api/tasks/{task_id}/stream") as stream:
        raw = "".join(stream.iter_text())
    events = _parse_sse(raw)
    types = [e["event"] for e in events]
    assert "task_started" in types
    assert "node_entered" in types
    assert "task_completed" in types
    seqs = [int(e["id"]) for e in events]
    assert seqs == sorted(seqs)
    assert seqs == list(range(len(seqs)))


def test_sse_reconnect_replays_from_after_seq(app_env: dict[str, Any]) -> None:
    client: TestClient = app_env["client"]
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
    _wait_status(client, task_id, "success", "failed", "fallback")

    with client.stream("GET", f"/api/tasks/{task_id}/stream") as stream:
        all_events = _parse_sse("".join(stream.iter_text()))
    assert len(all_events) >= 3
    cut = all_events[1]["data"]["seq"]
    with client.stream(
        "GET", f"/api/tasks/{task_id}/stream", params={"after_seq": cut}
    ) as stream:
        replay = _parse_sse("".join(stream.iter_text()))
    replay_seqs = [e["data"]["seq"] for e in replay]
    assert replay_seqs
    assert all(s > cut for s in replay_seqs)
    assert replay_seqs == [
        e["data"]["seq"] for e in all_events if e["data"]["seq"] > cut
    ]


def test_confirm_plan_idempotent(app_env: dict[str, Any]) -> None:
    client: TestClient = app_env["client"]
    dataset_id = _import_fixtures(client)
    submit = client.post(
        "/api/tasks",
        json={
            "dataset_id": dataset_id,
            "user_query": _QUERY,
            "strategy_flags": {
                "enable_human_confirm": True,
                "enable_planner": True,
            },
        },
    )
    task_id = submit.json()["task_id"]
    _wait_status(client, task_id, "waiting_confirm")

    body = {
        "task_id": task_id,
        "confirm_type": "plan",
        "decision": "accept",
        "idempotency_key": "idem-1",
    }
    r1 = client.post(f"/api/tasks/{task_id}/confirm-plan", json=body)
    r2 = client.post(f"/api/tasks/{task_id}/confirm-plan", json=body)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["mutated"] is True
    assert r2.json()["mutated"] is False
    assert r1.json()["decision"] == r2.json()["decision"] == "accept"

    gate = app_env["app"].state.runtime.confirm_gate
    assert gate.mutation_count(task_id) == 1
    _wait_status(client, task_id, "success", "failed", "fallback")


def test_terminate_kills_container_and_keeps_trace(tmp_path: Path) -> None:
    settings = Settings(sqlite_path=str(tmp_path / "term.db"))
    blockers: list[_BlockingExecutor] = []

    def _factory() -> _BlockingExecutor:
        exe = _BlockingExecutor()
        blockers.append(exe)
        return exe

    application = create_app(
        settings=settings,
        dataset_root=tmp_path / "datasets",
        sqlite_path=tmp_path / "term.db",
        upload_dir=tmp_path / "uploads",
        executor_factory=_factory,
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
        # Wait until execute has been entered (blocker waiting).
        deadline = time.time() + 10
        while time.time() < deadline and not blockers:
            time.sleep(0.05)
        assert blockers, "executor not created"
        while time.time() < deadline and blockers[0].calls < 1:
            time.sleep(0.05)
        assert blockers[0].calls >= 1

        term = client.post(
            f"/api/tasks/{task_id}/terminate",
            json={"task_id": task_id, "reason": "user_cancel"},
        )
        assert term.status_code == 200
        assert term.json()["executor_terminated"] is True
        assert blockers[0].terminated is True

        trace = _wait_status(client, task_id, "terminated")
        assert trace["terminate_reason"] == "user_cancel"
        assert isinstance(trace["events"], list)
        assert any(e["event_type"] == "task_terminated" for e in trace["events"])
        # Completed results retained (may be empty if killed mid-flight — field present).
        assert "sub_task_results" in trace
    finally:
        application.state.runtime.queue.shutdown()
        application.state.runtime.audit_store.close()


def test_concurrent_submit_second_task_queued(tmp_path: Path) -> None:
    settings = Settings(sqlite_path=str(tmp_path / "q.db"))
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
        sqlite_path=tmp_path / "q.db",
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
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["queue_position"] == 0
        assert r2.json()["queue_position"] >= 1
        t1, t2 = r1.json()["task_id"], r2.json()["task_id"]

        deadline = time.time() + 10
        while time.time() < deadline and len(blockers) < 1:
            time.sleep(0.05)
        assert len(blockers) == 1  # second must not start yet
        trace2 = client.get(f"/api/tasks/{t2}/trace").json()
        assert trace2["status"] in {"queued", "pending"}

        blockers[0].release()
        _wait_status(client, t1, "success", "failed", "fallback", "terminated")
        # Second should start after first finishes.
        deadline = time.time() + 10
        while time.time() < deadline and len(blockers) < 2:
            time.sleep(0.05)
        assert len(blockers) == 2
        blockers[1].release()
        _wait_status(client, t2, "success", "failed", "fallback", "terminated")
    finally:
        for b in blockers:
            b.release()
        application.state.runtime.queue.shutdown()
        application.state.runtime.audit_store.close()
