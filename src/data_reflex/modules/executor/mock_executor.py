# -*- coding: utf-8 -*-
"""Mock 沙箱：四种罐头结果，绝不执行 ``op_content``（评测/单测用）。

上游：测试注入或 ``SANDBOX_BACKEND=mock``。
本文件：SUCCESS / SYNTAX_ERROR / TIMEOUT / RESOURCE_LIMIT。
下游：与 Docker 执行器同一 ``ExecuteResp`` 契约，便于 Graph 联调。

See ``tasks.md`` T12 / D-EXEC-01。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from data_reflex.models.schemas import ExecuteReq, ExecuteResp
from data_reflex.modules.executor.base import SandboxRunConfig
from data_reflex.utils.logger import get_logger, summarize_op

logger = get_logger(__name__)


class MockScenario(StrEnum):
    """Mock 可发出的确定性结局。"""

    SUCCESS = "success"
    SYNTAX_ERROR = "syntax_error"
    TIMEOUT = "timeout"
    RESOURCE_LIMIT = "resource_limit"


class MockSandboxExecutor:
    """测试替身：记录配置、返回罐头行，不跑生成代码。"""

    def __init__(
        self,
        scenario: MockScenario = MockScenario.SUCCESS,
        *,
        network_disabled: bool = True,
        success_rows: list[dict[str, Any]] | None = None,
        success_columns: list[str] | None = None,
        duration_ms: int = 12,
    ) -> None:
        self._scenario = scenario
        self._network_disabled = network_disabled
        self._success_rows = success_rows or [
            {"order_id": "O001", "amount": 100.0},
            {"order_id": "O002", "amount": 200.0},
        ]
        self._success_columns = success_columns or ["order_id", "amount"]
        self._duration_ms = duration_ms
        self._terminated = False
        self._terminate_reason: str | None = None
        self.last_run_config: SandboxRunConfig | None = None
        self.last_req: ExecuteReq | None = None
        self.executed_op_contents: list[str] = []

    @property
    def scenario(self) -> MockScenario:
        return self._scenario

    def set_scenario(self, scenario: MockScenario) -> None:
        self._scenario = scenario

    @property
    def network_disabled(self) -> bool:
        return self._network_disabled

    @property
    def terminated(self) -> bool:
        return self._terminated

    @property
    def terminate_reason(self) -> str | None:
        return self._terminate_reason

    def build_run_config(self, req: ExecuteReq) -> SandboxRunConfig:
        return SandboxRunConfig(
            timeout_seconds=req.timeout_seconds,
            memory_limit_mb=req.memory_limit_mb,
            cpu_limit=req.cpu_limit,
            network_disabled=self._network_disabled,
        )

    def terminate(self, reason: str = "") -> None:
        self._terminated = True
        self._terminate_reason = reason or "terminated"
        logger.info(
            "mock sandbox terminate",
            extra={"task_id": "-", "dataset_id": "-", "reason": self._terminate_reason},
        )

    def execute(self, req: ExecuteReq) -> ExecuteResp:
        """Return a canned ``ExecuteResp``; do not eval/exec ``op_content``."""
        if self._terminated:
            return ExecuteResp(
                success=False,
                stderr="sandbox terminated before execute",
                exit_code=137,
                duration_ms=0,
                resource_killed=False,
            )

        config = self.build_run_config(req)
        self.last_run_config = config
        self.last_req = req
        # Record metadata only — never append raw op_content for safety tests.
        self.executed_op_contents.append(f"{req.op_type}:len={len(req.op_content)}")

        logger.info(
            "mock sandbox execute",
            extra={
                "task_id": "-",
                "dataset_id": req.dataset_id,
                "scenario": self._scenario.value,
                **summarize_op(req.op_type, req.op_content),
                "timeout_seconds": config.timeout_seconds,
                "memory_limit_mb": config.memory_limit_mb,
                "cpu_limit": config.cpu_limit,
                "network_disabled": config.network_disabled,
            },
        )

        if self._scenario is MockScenario.SUCCESS:
            return ExecuteResp(
                success=True,
                result_rows=list(self._success_rows),
                result_columns=list(self._success_columns),
                stdout="mock ok",
                stderr="",
                exit_code=0,
                duration_ms=self._duration_ms,
                resource_killed=False,
            )

        if self._scenario is MockScenario.SYNTAX_ERROR:
            kind = "SQL" if req.op_type == "sql" else "Python"
            return ExecuteResp(
                success=False,
                result_rows=[],
                result_columns=[],
                stdout="",
                stderr=f"{kind} syntax error near unexpected token (mock)",
                exit_code=1,
                duration_ms=self._duration_ms,
                resource_killed=False,
            )

        if self._scenario is MockScenario.TIMEOUT:
            return ExecuteResp(
                success=False,
                result_rows=[],
                result_columns=[],
                stdout="",
                stderr=(
                    f"execution timed out after {config.timeout_seconds}s "
                    "(mock timeout)"
                ),
                exit_code=124,
                duration_ms=config.timeout_seconds * 1000,
                resource_killed=True,
            )

        # RESOURCE_LIMIT
        return ExecuteResp(
            success=False,
            result_rows=[],
            result_columns=[],
            stdout="",
            stderr=(
                f"resource limit exceeded: memory_limit_mb="
                f"{config.memory_limit_mb} cpu_limit={config.cpu_limit} "
                "(mock resource kill)"
            ),
            exit_code=137,
            duration_ms=self._duration_ms,
            resource_killed=True,
        )
