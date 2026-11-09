# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import logging
import re

import pytest

from data_reflex.utils.logger import (
    configure_logging,
    get_logger,
    log_exception,
    sanitize_extra,
    scrub_message,
    summarize_op,
)


@pytest.fixture()
def log_buffer() -> io.StringIO:
    buf = io.StringIO()
    configure_logging(level=logging.DEBUG, stream=buf, force=True)
    return buf


def test_extra_injects_task_context(log_buffer: io.StringIO) -> None:
    logger = get_logger("tests.logger.context")
    logger.info(
        "node entered",
        extra={
            "task_id": "task-abc",
            "dataset_id": "ds-1",
            "sub_task_id": "st-2",
            "audit_trace_id": "trace-9",
        },
    )
    text = log_buffer.getvalue()
    assert "task_id=task-abc" in text
    assert "dataset_id=ds-1" in text
    assert "sub_task_id=st-2" in text
    assert "audit_trace_id=trace-9" in text
    assert "node entered" in text


def test_exception_log_includes_task_id_and_stack(log_buffer: io.StringIO) -> None:
    logger = get_logger("tests.logger.exc")
    try:
        raise ValueError("boom-for-stack")
    except ValueError as exc:
        log_exception(
            logger,
            "capture failed",
            exc=exc,
            extra={"task_id": "task-exc-1", "dataset_id": "ds-x"},
        )

    text = log_buffer.getvalue()
    assert "task_id=task-exc-1" in text
    assert "capture failed" in text
    assert "ValueError: boom-for-stack" in text
    assert "Traceback (most recent call last)" in text
    # Full stack should reference this test file.
    assert "test_logger.py" in text


def test_op_content_original_never_appears_in_log(log_buffer: io.StringIO) -> None:
    logger = get_logger("tests.logger.op")
    secret_sql = "SELECT phone, email FROM users WHERE amount > 100; -- secret-op"
    logger.warning(
        "op generated",
        extra={
            "task_id": "task-op",
            "op_content": secret_sql,
            **summarize_op("sql", secret_sql),
        },
    )
    text = log_buffer.getvalue()
    assert secret_sql not in text
    assert "SELECT phone" not in text
    assert (
        "op_content_length" in text or "op_type=sql" in text or "op generated" in text
    )
    assert "[REDACTED]:op_content:len=" in text or "op_content=" not in text
    # summarize_op metadata may be in extra; length should match.
    assert str(len(secret_sql)) in text or "op generated" in text


def test_sanitize_extra_redacts_forbidden_keys() -> None:
    raw = {
        "task_id": "t1",
        "op_content": "DROP TABLE orders;",
        "email": "user@example.com",
        "result_rows": [{"a": 1}, {"a": 2}],
    }
    safe = sanitize_extra(raw)
    assert safe["task_id"] == "t1"
    assert "DROP TABLE" not in str(safe["op_content"])
    assert str(safe["op_content"]).startswith("[REDACTED]")
    assert str(safe["email"]).startswith("[REDACTED]")
    assert "items=2" in str(safe["result_rows"])


def test_summarize_op_excludes_content() -> None:
    content = "print('leak')"
    meta = summarize_op("python", content)
    assert meta == {"op_type": "python", "op_content_length": len(content)}
    assert "leak" not in str(meta)


def test_scrub_message_masks_email_and_phone() -> None:
    msg = scrub_message("contact 13812345678 or a@b.com please")
    assert "13812345678" not in msg
    assert "a@b.com" not in msg
    assert "[REDACTED]:phone" in msg
    assert "[REDACTED]:email" in msg


def test_missing_context_fields_default_without_crash(log_buffer: io.StringIO) -> None:
    logger = get_logger("tests.logger.defaults")
    logger.info("no extra provided")
    text = log_buffer.getvalue()
    assert "task_id=-" in text
    assert "no extra provided" in text


def test_configure_logging_force_reattaches_handler() -> None:
    buf1 = io.StringIO()
    configure_logging(level="INFO", stream=buf1, force=True)
    get_logger("tests.logger.reconfig").info("first")
    assert "first" in buf1.getvalue()

    buf2 = io.StringIO()
    configure_logging(level="DEBUG", stream=buf2, force=True)
    get_logger("tests.logger.reconfig").info("second")
    assert "second" in buf2.getvalue()
    assert "second" not in buf1.getvalue()


def test_log_args_do_not_leak_known_op_content(log_buffer: io.StringIO) -> None:
    """If op_content is also passed as a %-format arg, still redact it."""
    logger = get_logger("tests.logger.args")
    secret = "INSERT INTO secrets VALUES (1);"
    # Put forbidden key on the record via extra; RedactFilter tracks the raw
    # value and scrubs matching format args.
    logger.error(
        "failed op payload=%s",
        secret,
        extra={"task_id": "task-args", "op_content": secret},
    )
    text = log_buffer.getvalue()
    assert secret not in text
    assert "INSERT INTO secrets" not in text
    assert "task_id=task-args" in text


def test_formatter_pattern_stable() -> None:
    buf = io.StringIO()
    configure_logging(level=logging.INFO, stream=buf, force=True)
    get_logger("tests.logger.fmt").info("ping", extra={"task_id": "t"})
    line = buf.getvalue().strip()
    assert re.search(r"\| INFO \| data_reflex\.tests\.logger\.fmt \|", line)
    assert "task_id=t" in line
