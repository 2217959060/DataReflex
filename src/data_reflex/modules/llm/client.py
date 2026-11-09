# -*- coding: utf-8 -*-
"""OpenAI 兼容聊天客户端（智谱 GLM 等）：只返回文本，不执行生成代码。

上游：GenerateOp / 澄清润色 / Reflect hint / 结论抛光等节点。
本文件：``chat.completions``；失败抛 ``LlmError`` 供调用方模板降级。
下游：调用方可记 Langfuse ``generation``（模型名与 usage，不回灌完整 prompt）。

See roadmap V2-1 / V2-7。
"""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlparse

from data_reflex.config.settings import Settings, get_settings
from data_reflex.utils.logger import get_logger
from data_reflex.utils.tracing import get_tracer

logger = get_logger(__name__)


class LlmError(RuntimeError):
    """传输/协议失败；调用方应回退模板，勿让任务硬崩。"""


class LlmClient(Protocol):
    def chat(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int = 512,
    ) -> str: ...


def _host_only(base_url: str) -> str:
    try:
        return urlparse(base_url).netloc or base_url
    except Exception:  # noqa: BLE001
        return base_url


def _extract_usage(resp: Any) -> dict[str, int] | None:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return None
    out: dict[str, int] = {}
    pairs = (
        ("input", "prompt_tokens"),
        ("output", "completion_tokens"),
        ("total", "total_tokens"),
    )
    for dest, attr in pairs:
        raw = getattr(usage, attr, None)
        if raw is None and isinstance(usage, dict):
            raw = usage.get(attr)
        try:
            if raw is not None:
                out[dest] = int(raw)
        except (TypeError, ValueError):
            continue
    return out or None


class OpenAICompatClient:
    """同步封装 openai SDK 的 chat.completions（读 settings 中的 model/base_url）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def chat(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int = 512,
    ) -> str:
        if not self._settings.llm_api_key.strip():
            raise LlmError("LLM_API_KEY is empty")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise LlmError("openai package is not installed") from exc

        client = OpenAI(
            api_key=self._settings.llm_api_key,
            base_url=self._settings.llm_base_url,
        )
        temp = (
            self._settings.llm_temperature
            if temperature is None
            else float(temperature)
        )
        model = self._settings.llm_model
        tracer = get_tracer()
        with tracer.span(
            "llm.chat",
            as_type="generation",
            model=model,
            metadata={
                "model": model,
                "host": _host_only(self._settings.llm_base_url),
                "message_count": len(messages),
            },
            input={"message_count": len(messages)},
        ) as span:
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=temp,
                    max_tokens=max_tokens,
                )
            except Exception as exc:  # noqa: BLE001 — map any SDK/network error
                span.update(
                    status_message=f"{type(exc).__name__}: {exc}"[:200],
                    metadata={"error": type(exc).__name__},
                )
                raise LlmError(f"{type(exc).__name__}: {exc}") from exc

            try:
                choice = resp.choices[0]
                message = choice.message
                content = getattr(message, "content", None)
                if content is None:
                    content = getattr(message, "reasoning_content", None) or ""
                text = str(content)
            except (IndexError, AttributeError, TypeError) as exc:
                span.update(
                    status_message=f"{type(exc).__name__}: {exc}"[:200],
                    metadata={"error": type(exc).__name__},
                )
                raise LlmError(f"unexpected chat response shape: {exc}") from exc

            usage = _extract_usage(resp)
            out_meta: dict[str, Any] = {"chars": len(text)}
            if usage:
                out_meta.update(usage)
            span.update(
                output=out_meta,
                metadata=out_meta,
                usage_details=usage,
                model=model,
            )
            logger.info(
                "llm chat ok",
                extra={
                    "task_id": "-",
                    "dataset_id": "-",
                    "model": model,
                    "chars": len(text),
                },
            )
            return text


class StaticLlmClient:
    """测试替身：返回固定字符串或按配置抛错。"""

    def __init__(self, reply: str = "", *, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int = 512,
    ) -> str:
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        tracer = get_tracer()
        with tracer.span(
            "llm.chat",
            as_type="generation",
            model="static-test",
            metadata={"model": "static-test", "message_count": len(messages)},
            input={"message_count": len(messages)},
        ) as span:
            if self.error is not None:
                span.update(
                    status_message=type(self.error).__name__,
                    metadata={"error": type(self.error).__name__},
                )
                raise self.error
            span.update(output={"chars": len(self.reply)})
            return self.reply


def build_llm_client(settings: Settings | None = None) -> LlmClient:
    """构造生产用 OpenAI 兼容客户端。"""
    return OpenAICompatClient(settings=settings)
