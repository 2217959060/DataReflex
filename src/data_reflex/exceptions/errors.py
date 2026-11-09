# -*- coding: utf-8 -*-
"""业务异常：类名以 Error 结尾；支持 ``raise ... from e`` 链式抛出。

API 层统一转换成错误响应；节点/模块用具体子类表达失败原因。
"""

from __future__ import annotations


class DataReflexError(Exception):
    """Root business exception (canonical name; see docs D-EXC-01)."""


# D-EXC-01: tasks.md names DataAgentError; alias keeps both docs valid.
DataAgentError = DataReflexError


class DataImportError(DataReflexError):
    """Data file import or structure recognition failed."""


class SchemaParseError(DataReflexError):
    """Table/column schema could not be parsed or validated."""


class IntentParseError(DataReflexError):
    """Natural-language intent parsing failed."""


class PlanBuildError(DataReflexError):
    """Task plan construction failed."""


class PlanValidateError(DataReflexError):
    """Plan validation failed (cycle, missing inputs, out of scope)."""


class OpGenerateError(DataReflexError):
    """SQL/Python operation generation failed."""


class SafetyViolationError(DataReflexError):
    """Dangerous write, unauthorized column, or unparseable op blocked."""


class SandboxUnavailableError(DataReflexError):
    """Docker sandbox cannot start; host execution is forbidden."""


class ExecutionTimeoutError(DataReflexError):
    """Sandbox execution exceeded the configured timeout."""


class ResourceLimitError(DataReflexError):
    """Sandbox hit memory/CPU/process limits."""


class SelfHealMeltedError(DataReflexError):
    """Self-heal circuit breaker tripped (fingerprint / retry / LLM cap)."""


class ResultValidateError(DataReflexError):
    """Result failed structured constraint validation."""


class ReportBuildError(DataReflexError):
    """Final or fallback report assembly failed."""


class ContextManageError(DataReflexError):
    """Conversation context management failed."""


class TaskQueueError(DataReflexError):
    """Single-task queue accept/schedule failed."""
