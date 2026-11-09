# -*- coding: utf-8 -*-
"""沙箱执行器协议与共用运行配置（Mock / Docker 的共同契约）。

See ``tasks.md`` T12 / D-EXEC-01。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from data_reflex.models.schemas import ExecuteReq, ExecuteResp


@dataclass(frozen=True, slots=True)
class SandboxRunConfig:
    """Resource / isolation knobs applied to a single execution.

    ``network_disabled`` is not on ``ExecuteReq`` (design IO fixed); executors
    always carry it from construction/settings — v1 default ``True``.
    """

    timeout_seconds: int
    memory_limit_mb: int
    cpu_limit: float
    network_disabled: bool = True


@runtime_checkable
class SandboxExecutor(Protocol):
    """Isolation boundary for generated SQL/Python — never run on the host."""

    def execute(self, req: ExecuteReq) -> ExecuteResp:
        """Run ``req`` inside an isolated environment and return structured results."""

    def terminate(self, reason: str = "") -> None:
        """Abort in-flight work (kill container / cancel mock). Idempotent."""

    def build_run_config(self, req: ExecuteReq) -> SandboxRunConfig:
        """Materialize limits from the request (+ executor isolation flags)."""
