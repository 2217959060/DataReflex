# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

import pytest

from data_reflex.utils.tracing import (
    SpanHandle,
    Tracer,
    clip_query_for_trace,
    create_tracer,
    get_tracer,
    is_langfuse_configured,
    reset_tracer,
)


class _FakeObservation:
    def __init__(self, name: str, *, trace_id: str, span_id: str) -> None:
        self.name = name
        self.trace_id = trace_id
        self.id = span_id
        self.ended = False
        self.updates: list[dict[str, Any]] = []

    def update(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)

    def end(self) -> None:
        self.ended = True


class _FakeLangfuse:
    """Test double matching the minimal Langfuse surface we call."""

    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.observations: list[_FakeObservation] = []
        self.flush_calls = 0
        self.shutdown_calls = 0
        self._n = 0

    def start_observation(self, **kwargs: Any) -> _FakeObservation:
        self.started.append(kwargs)
        self._n += 1
        ctx = kwargs.get("trace_context") or {}
        trace_id = str(ctx.get("trace_id") or f"trace-{self._n}")
        obs = _FakeObservation(
            str(kwargs.get("name", "")),
            trace_id=trace_id,
            span_id=f"span-{self._n}",
        )
        self.observations.append(obs)
        return obs

    def flush(self) -> None:
        self.flush_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class _BrokenLangfuse(_FakeLangfuse):
    def start_observation(self, **kwargs: Any) -> _FakeObservation:
        raise RuntimeError("boom-start")


@pytest.fixture(autouse=True)
def _isolate_tracer() -> Any:
    reset_tracer(None)
    yield
    reset_tracer(None)


def test_noop_without_langfuse_config() -> None:
    tracer = create_tracer(public_key="", secret_key="", host="")
    assert tracer.enabled is False
    with tracer.trace("task-a", metadata={"task_id": "t1"}) as root:
        assert isinstance(root, SpanHandle)
        with tracer.span("Node_Plan") as child:
            child.update(output={"steps": 2})
    assert tracer.records == []
    tracer.flush()  # must not raise
    tracer.shutdown()


def test_mock_client_records_spans() -> None:
    fake = _FakeLangfuse()
    tracer = create_tracer(
        public_key="pk-test",
        secret_key="sk-test",
        host="http://localhost:3000",
        client=fake,
    )
    assert tracer.enabled is True

    with tracer.trace("task-b", metadata={"task_id": "t2", "dataset_id": "ds"}) as root:
        root.update(output={"status": "ok"})
        with tracer.span("Node_Execute", metadata={"sub_task_id": "st-1"}):
            pass
        with tracer.span("llm.generate", as_type="generation"):
            pass

    assert len(tracer.records) == 3
    # Children finish before parent (append in finally → LIFO by exit).
    assert [r.name for r in tracer.records] == [
        "Node_Execute",
        "llm.generate",
        "task-b",
    ]
    assert tracer.records[-1].kind == "trace"
    assert tracer.records[0].parent_name == "task-b"
    assert tracer.records[1].kind == "generation"

    assert len(fake.started) == 3
    assert all(obs.ended for obs in fake.observations)
    tracer.flush()
    assert fake.flush_calls == 1


def test_keys_without_client_still_enable_when_constructible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured keys + injected client path already covered; keys alone gate."""
    assert is_langfuse_configured(public_key="pk", secret_key="sk") is True
    assert is_langfuse_configured(public_key="", secret_key="sk") is False
    assert is_langfuse_configured(public_key="pk", secret_key="  ") is False


def test_langfuse_errors_do_not_break_caller() -> None:
    tracer = create_tracer(client=_BrokenLangfuse())
    with tracer.trace("task-c"), tracer.span("fragile"):
        pass
    assert tracer.enabled is True
    assert len(tracer.records) == 2
    assert all(r.status == "ok" for r in tracer.records)


def test_exception_inside_span_marks_error_and_rethrows() -> None:
    fake = _FakeLangfuse()
    tracer = create_tracer(client=fake)
    with (
        pytest.raises(ValueError, match="planned-fail"),
        tracer.span("Node_Fail"),
    ):
        raise ValueError("planned-fail")
    assert len(tracer.records) == 1
    assert tracer.records[0].status == "error"
    assert tracer.records[0].error is not None
    assert "planned-fail" in tracer.records[0].error
    assert fake.observations[0].ended is True


def test_metadata_redacts_forbidden_keys() -> None:
    fake = _FakeLangfuse()
    tracer = create_tracer(client=fake)
    with tracer.span(
        "safe",
        metadata={"task_id": "t9", "op_content": "SELECT secret FROM x"},
    ):
        pass
    meta = tracer.records[0].metadata
    assert meta["task_id"] == "t9"
    assert "SELECT" not in str(meta.get("op_content", ""))
    assert "REDACTED" in str(meta.get("op_content", ""))


def test_get_tracer_caches_and_reset() -> None:
    noop = create_tracer()
    reset_tracer(noop)
    assert get_tracer() is noop
    other = Tracer(enabled=False)
    reset_tracer(other)
    assert get_tracer() is other


def test_child_observation_passes_trace_context() -> None:
    fake = _FakeLangfuse()
    tracer = create_tracer(client=fake)
    with tracer.trace("task-root"):
        with tracer.span("Node_A"):
            with tracer.span("llm.chat", as_type="generation", model="glm-test"):
                pass
    assert len(fake.started) == 3
    root_kw = fake.started[0]
    child_kw = fake.started[1]
    gen_kw = fake.started[2]
    assert "trace_context" not in root_kw or root_kw.get("trace_context") is None
    assert child_kw["trace_context"]["trace_id"] == fake.observations[0].trace_id
    assert child_kw["trace_context"]["parent_span_id"] == fake.observations[0].id
    assert gen_kw["as_type"] == "generation"
    assert gen_kw.get("model") == "glm-test"
    assert gen_kw["trace_context"]["parent_span_id"] == fake.observations[1].id


def test_clip_query_for_trace() -> None:
    assert clip_query_for_trace("  a   b  ") == "a b"
    long = "x" * 200
    clipped = clip_query_for_trace(long, limit=20)
    assert len(clipped) <= 20
    assert clipped.endswith("…")


def test_generation_update_accepts_usage_details() -> None:
    fake = _FakeLangfuse()
    tracer = create_tracer(client=fake)
    with tracer.span("llm.chat", as_type="generation", model="m") as span:
        span.update(
            output={"chars": 3},
            usage_details={"input": 1, "output": 2, "total": 3},
            model="m",
        )
    updates = fake.observations[0].updates
    assert any(
        u.get("usage_details", {}).get("total") == 3 for u in updates
    )
