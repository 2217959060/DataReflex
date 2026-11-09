# -*- coding: utf-8 -*-
"""结构化日志：注入 task/dataset 上下文，并对敏感内容脱敏。

业务模块用法::

    from data_reflex.utils.logger import get_logger, log_exception, summarize_op

    logger = get_logger(__name__)
    logger.info("plan ready", extra={"task_id": tid, "dataset_id": did})
    log_exception(logger, "node failed", extra={"task_id": tid}, exc=err)

禁止记录 ``op_content`` 原文、原始数据行或敏感字段值；改用 ``summarize_op`` /
``sanitize_extra``。See D-LOG-01。
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping
from typing import Any

# Fields rendered in the structured format (defaults.md / conventions §5).
CONTEXT_FIELDS: tuple[str, ...] = (
    "task_id",
    "dataset_id",
    "sub_task_id",
    "audit_trace_id",
)

# Keys that must never appear as raw values in log records / extra.
_FORBIDDEN_EXTRA_KEYS: frozenset[str] = frozenset(
    {
        "op_content",
        "result_rows",
        "sample_values",
        "raw_row",
        "raw_rows",
        "raw_data",
        "rows",
        "phone",
        "mobile",
        "email",
        "id_card",
        "address",
        "手机",
        "手机号",
        "电话",
        "邮箱",
        "身份证",
        "身份证号",
        "地址",
    }
)

_REDACTED = "[REDACTED]"
_PACKAGE_LOGGER_NAME = "data_reflex"
_configured = False

# Rough patterns for accidental value leaks in free-text messages.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")


def _redact_value(key: str, value: object) -> str:
    """Replace a forbidden value with a length-only placeholder."""
    if value is None:
        return f"{_REDACTED}:{key}:none"
    if isinstance(value, str):
        return f"{_REDACTED}:{key}:len={len(value)}"
    if isinstance(value, (list, tuple, set)):
        return f"{_REDACTED}:{key}:items={len(value)}"
    if isinstance(value, Mapping):
        return f"{_REDACTED}:{key}:keys={len(value)}"
    return f"{_REDACTED}:{key}:type={type(value).__name__}"


def sanitize_extra(extra: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy ``extra``, redacting forbidden keys; keep context fields intact."""
    if not extra:
        return {}
    out: dict[str, Any] = {}
    for key, value in extra.items():
        if key in _FORBIDDEN_EXTRA_KEYS:
            out[key] = _redact_value(key, value)
        else:
            out[key] = value
    return out


def summarize_op(op_type: str, op_content: str) -> dict[str, Any]:
    """Safe metadata for generated ops — never includes content text."""
    return {
        "op_type": op_type,
        "op_content_length": len(op_content),
    }


def scrub_message(message: str, known_secrets: list[str] | None = None) -> str:
    """Scrub emails/phones and any known secret substrings from a message."""
    text = message
    if known_secrets:
        for secret in known_secrets:
            if secret and secret in text:
                text = text.replace(secret, f"{_REDACTED}:len={len(secret)}")
    text = _EMAIL_RE.sub(f"{_REDACTED}:email", text)
    text = _PHONE_RE.sub(f"{_REDACTED}:phone", text)
    return text


class ContextDefaultFilter(logging.Filter):
    """Ensure context attrs exist so the formatter never KeyErrors."""

    def filter(self, record: logging.LogRecord) -> bool:
        for field in CONTEXT_FIELDS:
            if not hasattr(record, field):
                setattr(record, field, "-")
        return True


class RedactFilter(logging.Filter):
    """Redact forbidden extras and scrub accidental secrets in the message."""

    def filter(self, record: logging.LogRecord) -> bool:
        known: list[str] = []
        for key in list(record.__dict__.keys()):
            if key in _FORBIDDEN_EXTRA_KEYS:
                raw = record.__dict__[key]
                if isinstance(raw, str) and raw and not str(raw).startswith(_REDACTED):
                    known.append(raw)
                record.__dict__[key] = _redact_value(key, raw)

        if isinstance(record.msg, str):
            record.msg = scrub_message(record.msg, known_secrets=known or None)

        if record.args:
            if isinstance(record.args, Mapping):
                cleaned: dict[str, Any] = {}
                for k, v in record.args.items():
                    if k in _FORBIDDEN_EXTRA_KEYS:
                        if isinstance(v, str) and v:
                            known.append(v)
                        cleaned[k] = _redact_value(k, v)
                    elif isinstance(v, str):
                        cleaned[k] = scrub_message(v, known_secrets=known or None)
                    else:
                        cleaned[k] = v
                record.args = cleaned
            elif isinstance(record.args, tuple):
                new_args: list[Any] = []
                for arg in record.args:
                    if isinstance(arg, str):
                        # If arg equals a known forbidden raw value, redact fully.
                        if arg in known:
                            new_args.append(f"{_REDACTED}:len={len(arg)}")
                        else:
                            new_args.append(
                                scrub_message(arg, known_secrets=known or None)
                            )
                    else:
                        new_args.append(arg)
                record.args = tuple(new_args)

        return True


class StructuredFormatter(logging.Formatter):
    """One-line structured format with injectable context fields."""

    default_fmt = (
        "%(asctime)s | %(levelname)s | %(name)s | "
        "task_id=%(task_id)s dataset_id=%(dataset_id)s "
        "sub_task_id=%(sub_task_id)s audit_trace_id=%(audit_trace_id)s | "
        "%(message)s"
    )

    def __init__(self, fmt: str | None = None, datefmt: str | None = None) -> None:
        super().__init__(fmt=fmt or self.default_fmt, datefmt=datefmt)


def configure_logging(
    *,
    level: int | str | None = None,
    stream: Any | None = None,
    force: bool = False,
) -> logging.Logger:
    """Configure the ``data_reflex`` package logger once (idempotent).

    Parameters
    ----------
    level:
        Log level name or int; default from settings ``log_level`` when available.
    stream:
        Optional stream for the StreamHandler (tests pass ``io.StringIO``).
    force:
        Reconfigure even if already configured (used by tests).
    """
    global _configured

    package = logging.getLogger(_PACKAGE_LOGGER_NAME)
    if _configured and not force:
        return package

    if level is None:
        try:
            from data_reflex.config.settings import get_settings

            level = get_settings().log_level
        except Exception:  # noqa: BLE001 — logging must not fail on settings
            level = "INFO"

    resolved = (
        level if isinstance(level, int) else logging.getLevelName(str(level).upper())
    )
    if isinstance(resolved, str):
        # getLevelName can return "Level X" for unknowns; fall back to INFO.
        resolved = logging.INFO

    package.setLevel(resolved)
    package.handlers.clear()
    package.propagate = False

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setLevel(resolved)
    handler.addFilter(ContextDefaultFilter())
    handler.addFilter(RedactFilter())
    handler.setFormatter(StructuredFormatter())
    package.addHandler(handler)

    _configured = True
    return package


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child logger under ``data_reflex``, ensuring config is applied."""
    configure_logging()
    if not name or name == _PACKAGE_LOGGER_NAME:
        return logging.getLogger(_PACKAGE_LOGGER_NAME)
    if name.startswith(f"{_PACKAGE_LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_PACKAGE_LOGGER_NAME}.{name}")


def log_exception(
    logger: logging.Logger,
    message: str,
    *,
    exc: BaseException | None = None,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Log an error with full traceback and sanitized context extras.

    Always sets ``exc_info`` so acceptance checks can assert stack presence.
    """
    safe = sanitize_extra(extra)
    # Prefer explicit exception; otherwise use current exception context.
    if exc is not None:
        logger.error(message, extra=safe, exc_info=(type(exc), exc, exc.__traceback__))
    else:
        logger.error(message, extra=safe, exc_info=True)


def bind_context(**context: Any) -> dict[str, Any]:
    """Build a sanitized ``extra`` dict limited to known context + safe keys."""
    return sanitize_extra(context)
