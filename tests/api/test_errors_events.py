# -*- coding: utf-8 -*-
"""Coverage for API error envelope and TaskEventLog helpers."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from data_reflex.api.app import create_app
from data_reflex.api.errors import error_body
from data_reflex.api.events import TaskEventLog
from data_reflex.config.settings import Settings
from data_reflex.exceptions import DataImportError
from data_reflex.main import main
from data_reflex.modules.executor.mock_executor import MockSandboxExecutor, MockScenario
from data_reflex.utils.tracing import create_tracer, reset_tracer


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


def test_error_body_includes_optional_detail() -> None:
    body = error_body(code="x", message="m", detail={"a": 1})
    assert body["error"]["code"] == "x"
    assert body["error"]["detail"] == {"a": 1}
    bare = error_body(code="y", message="n")
    assert "detail" not in bare["error"]


def test_http_and_validation_error_envelope(tmp_path: Any) -> None:
    app = create_app(
        settings=Settings(sqlite_path=str(tmp_path / "e.db")),
        dataset_root=tmp_path / "datasets",
        sqlite_path=tmp_path / "e.db",
        upload_dir=tmp_path / "uploads",
        executor_factory=lambda: MockSandboxExecutor(MockScenario.SUCCESS),
    )
    client = TestClient(app, raise_server_exceptions=False)
    try:
        missing = client.get("/api/tasks/no-such-task/trace")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"].startswith("http_")

        bad = client.post("/api/tasks", json={"dataset_id": 1})  # wrong types
        assert bad.status_code == 422
        assert bad.json()["error"]["code"] == "validation_error"
    finally:
        app.state.runtime.queue.shutdown()
        app.state.runtime.audit_store.close()


def test_business_error_envelope(tmp_path: Any) -> None:
    app = create_app(
        settings=Settings(sqlite_path=str(tmp_path / "b.db")),
        dataset_root=tmp_path / "datasets",
        sqlite_path=tmp_path / "b.db",
        upload_dir=tmp_path / "uploads",
        executor_factory=lambda: MockSandboxExecutor(MockScenario.SUCCESS),
    )

    @app.get("/_boom")
    def _boom() -> None:
        raise DataImportError("import denied")

    client = TestClient(app, raise_server_exceptions=False)
    try:
        resp = client.get("/_boom")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "DataImportError"
    finally:
        app.state.runtime.queue.shutdown()
        app.state.runtime.audit_store.close()


def test_event_log_wait_after_and_iter_live() -> None:
    log = TaskEventLog("t-ev")
    assert log.next_seq == 0
    empty = log.wait_after(-1, timeout=0.05)
    assert empty == []

    def _late_emit() -> None:
        time.sleep(0.05)
        log.emit("task_started", {"ok": True})
        log.close()

    threading.Thread(target=_late_emit, daemon=True).start()
    got = list(log.iter_live(after_seq=-1, poll_timeout=0.1))
    assert len(got) >= 1
    assert got[0].event_type == "task_started"
    assert got[0].seq == 0


def test_main_entrypoint_prints(capsys: pytest.CaptureFixture[str]) -> None:
    main()
    out = capsys.readouterr().out
    assert "uvicorn data_reflex.main:app" in out
