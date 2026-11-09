# -*- coding: utf-8 -*-
"""Langfuse 追踪封装：有 Key 上报，无 Key 则 no-op，绝不阻断主流程。

上游：Queue 开任务根 Trace；BaseNode / LLM client 打 span、generation。
本文件：``Tracer.trace`` / ``span`` / ``flush``；双 Key 才启用客户端。
下游：Langfuse Cloud UI；本地 ``SpanRecord`` 供单测断言。

``LANGFUSE_HOST`` 可空（Cloud 默认）；异常只打 warning。

See ``tasks.md`` T17 / D-TRACE-01；roadmap V2-7 / D-V2-OBS-01。
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

from data_reflex.utils.logger import get_logger, sanitize_extra

logger = get_logger(__name__)

SpanKind = Literal["trace", "span", "generation"]
SpanStatus = Literal["ok", "error"]

# Local parent names (for SpanRecord) and live Langfuse observations (for nesting).
_span_stack: ContextVar[tuple[str, ...]] = ContextVar("dr_span_stack", default=())
_obs_stack: ContextVar[tuple[Any, ...]] = ContextVar("dr_obs_stack", default=())


@dataclass
class SpanRecord:
    """进程内 span/trace 记录（单测与本地排查用）。"""

    name: str
    kind: SpanKind
    metadata: dict[str, Any] = field(default_factory=dict)
    input: Any | None = None
    output: Any | None = None
    status: SpanStatus = "ok"
    error: str | None = None
    parent_name: str | None = None
    duration_ms: float = 0.0


class SpanHandle:
    """``trace`` / ``span`` 上下文给出的可变句柄（可 update 输出/状态）。"""

    def __init__(self, record: SpanRecord, observation: Any | None = None) -> None:
        self._record = record
        self._observation = observation

    @property
    def record(self) -> SpanRecord:
        return self._record

    @property
    def name(self) -> str:
        return self._record.name

    def update(
        self,
        *,
        output: Any | None = None,
        metadata: Mapping[str, Any] | None = None,
        status_message: str | None = None,
        usage_details: Mapping[str, int] | None = None,
        model: str | None = None,
    ) -> None:
        if output is not None:
            self._record.output = output
        if metadata:
            self._record.metadata.update(sanitize_extra(dict(metadata)))
        if self._observation is None:
            return
        try:
            kwargs: dict[str, Any] = {}
            if output is not None:
                kwargs["output"] = output
            if metadata:
                kwargs["metadata"] = sanitize_extra(dict(metadata))
            if status_message is not None:
                kwargs["status_message"] = status_message
            if usage_details is not None:
                kwargs["usage_details"] = dict(usage_details)
            if model is not None:
                kwargs["model"] = model
            if kwargs and hasattr(self._observation, "update"):
                self._observation.update(**kwargs)
        except Exception as exc:  # noqa: BLE001 — never block callers
            logger.warning(
                "langfuse observation update failed",
                extra={"task_id": "-", "dataset_id": "-", "error": type(exc).__name__},
            )


class Tracer:
    """对外追踪器：已配置 Langfuse 则上报，否则全程空操作。"""

    def __init__(
        self,
        *,
        enabled: bool,
        client: Any | None = None,
    ) -> None:
        self.enabled = enabled
        self._client = client
        self.records: list[SpanRecord] = []

    @contextmanager
    def trace(
        self,
        name: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        input: Any | None = None,
    ) -> Iterator[SpanHandle]:
        """Root observation for a task / request."""
        with self._observe(
            name, kind="trace", metadata=metadata, input=input
        ) as handle:
            yield handle

    @contextmanager
    def span(
        self,
        name: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        input: Any | None = None,
        as_type: Literal["span", "generation"] = "span",
        model: str | None = None,
    ) -> Iterator[SpanHandle]:
        """Child observation (node / tool / generation)."""
        kind: SpanKind = "generation" if as_type == "generation" else "span"
        with self._observe(
            name,
            kind=kind,
            metadata=metadata,
            input=input,
            as_type=as_type,
            model=model,
        ) as handle:
            yield handle

    def flush(self) -> None:
        if self._client is None:
            return
        try:
            self._client.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "langfuse flush failed",
                extra={"task_id": "-", "dataset_id": "-", "error": type(exc).__name__},
            )

    def shutdown(self) -> None:
        if self._client is None:
            return
        try:
            self._client.shutdown()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "langfuse shutdown failed",
                extra={"task_id": "-", "dataset_id": "-", "error": type(exc).__name__},
            )

    @contextmanager
    def _observe(
        self,
        name: str,
        *,
        kind: SpanKind,
        metadata: Mapping[str, Any] | None,
        input: Any | None,
        as_type: Literal["span", "generation"] = "span",
        model: str | None = None,
    ) -> Iterator[SpanHandle]:
        clean_meta = sanitize_extra(dict(metadata or {}))
        parent_name = _span_stack.get()[-1] if _span_stack.get() else None
        parent_obs = _obs_stack.get()[-1] if _obs_stack.get() else None
        record = SpanRecord(
            name=name,
            kind=kind,
            metadata=clean_meta,
            input=input,
            parent_name=parent_name,
        )
        observation: Any | None = None
        if self.enabled and self._client is not None:
            observation = self._start_observation(
                name=name,
                as_type=as_type if kind != "trace" else "span",
                metadata=clean_meta,
                input=input,
                parent=parent_obs,
                model=model or (str(clean_meta.get("model")) if clean_meta.get("model") else None),
            )

        name_token = _span_stack.set(_span_stack.get() + (name,))
        obs_token = _obs_stack.set(
            _obs_stack.get() + ((observation,) if observation is not None else ())
        )
        started = time.perf_counter()
        handle = SpanHandle(record, observation)
        try:
            yield handle
        except Exception as exc:
            record.status = "error"
            record.error = f"{type(exc).__name__}: {exc}"[:240]
            self._safe_end(
                observation, output=None, level="ERROR", status_message=record.error
            )
            raise
        else:
            self._safe_end(observation, output=record.output)
        finally:
            record.duration_ms = round((time.perf_counter() - started) * 1000, 3)
            if self.enabled:
                self.records.append(record)
            _span_stack.reset(name_token)
            _obs_stack.reset(obs_token)

    def _start_observation(
        self,
        *,
        name: str,
        as_type: str,
        metadata: dict[str, Any],
        input: Any | None,
        parent: Any | None = None,
        model: str | None = None,
    ) -> Any | None:
        assert self._client is not None
        try:
            kwargs: dict[str, Any] = {
                "name": name,
                "as_type": as_type,
                "metadata": metadata or None,
                "input": input,
            }
            if model:
                kwargs["model"] = model
            if parent is not None:
                trace_id = getattr(parent, "trace_id", None)
                parent_id = getattr(parent, "id", None)
                if isinstance(trace_id, str) and trace_id:
                    ctx: dict[str, str] = {"trace_id": trace_id}
                    if isinstance(parent_id, str) and parent_id:
                        ctx["parent_span_id"] = parent_id
                    kwargs["trace_context"] = ctx
            return self._client.start_observation(**kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "langfuse start_observation failed",
                extra={"task_id": "-", "dataset_id": "-", "error": type(exc).__name__},
            )
            return None

    def _safe_end(
        self,
        observation: Any | None,
        *,
        output: Any | None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        if observation is None:
            return
        try:
            if hasattr(observation, "update"):
                kwargs: dict[str, Any] = {}
                if output is not None:
                    kwargs["output"] = output
                if level is not None:
                    kwargs["level"] = level
                if status_message is not None:
                    kwargs["status_message"] = status_message
                if kwargs:
                    observation.update(**kwargs)
            if hasattr(observation, "end"):
                observation.end()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "langfuse observation end failed",
                extra={"task_id": "-", "dataset_id": "-", "error": type(exc).__name__},
            )


def is_langfuse_configured(
    *,
    public_key: str = "",
    secret_key: str = "",
) -> bool:
    """公钥与私钥都非空才算已配置。"""
    return bool(public_key.strip() and secret_key.strip())


def create_tracer(
    *,
    public_key: str = "",
    secret_key: str = "",
    host: str = "",
    client: Any | None = None,
) -> Tracer:
    """构造 Tracer。

    - 注入 ``client`` → 启用（单测/自定义后端）
    - 双 Key 齐全 → 建 Langfuse 客户端（失败降级 no-op）
    - 否则 → 禁用 no-op
    """
    if client is not None:
        return Tracer(enabled=True, client=client)

    if not is_langfuse_configured(public_key=public_key, secret_key=secret_key):
        return Tracer(enabled=False, client=None)

    try:
        from langfuse import Langfuse

        lf_client = Langfuse(
            public_key=public_key.strip(),
            secret_key=secret_key.strip(),
            host=host.strip() or None,
        )
        return Tracer(enabled=True, client=lf_client)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "langfuse client init failed; tracing disabled",
            extra={"task_id": "-", "dataset_id": "-", "error": type(exc).__name__},
        )
        return Tracer(enabled=False, client=None)


_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    """进程级单例 Tracer（惰性按 settings 创建）。"""
    global _tracer
    if _tracer is None:
        from data_reflex.config.settings import get_settings

        settings = get_settings()
        _tracer = create_tracer(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    return _tracer


def reset_tracer(tracer: Tracer | None = None) -> None:
    """Replace or clear the process-wide tracer (tests / reconfigure)."""
    global _tracer
    if _tracer is not None and _tracer is not tracer:
        _tracer.shutdown()
    _tracer = tracer


def clip_query_for_trace(text: str, *, limit: int = 120) -> str:
    """Short, scrubbed user_query for root-trace metadata (no long dumps)."""
    from data_reflex.utils.logger import scrub_message

    t = " ".join(scrub_message(str(text or "")).split())
    if len(t) <= limit:
        return t
    return t[: limit - 1] + "…"
