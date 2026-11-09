# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from data_reflex.models.schemas import ExecuteReq
from data_reflex.modules.executor import (
    MockSandboxExecutor,
    MockScenario,
    SandboxExecutor,
    SandboxRunConfig,
)


def _req(**overrides: object) -> ExecuteReq:
    base: dict[str, object] = {
        "op_type": "sql",
        "op_content": "SELECT order_id, amount FROM orders",
        "dataset_id": "ds-mock",
        "timeout_seconds": 30,
        "memory_limit_mb": 256,
        "cpu_limit": 1.0,
    }
    base.update(overrides)
    return ExecuteReq.model_validate(base)


def test_protocol_runtime_checkable() -> None:
    executor = MockSandboxExecutor()
    assert isinstance(executor, SandboxExecutor)


def test_run_config_passes_timeout_memory_cpu_and_network_disabled() -> None:
    executor = MockSandboxExecutor(network_disabled=True)
    req = _req(timeout_seconds=45, memory_limit_mb=512, cpu_limit=0.5)
    cfg = executor.build_run_config(req)
    assert cfg == SandboxRunConfig(
        timeout_seconds=45,
        memory_limit_mb=512,
        cpu_limit=0.5,
        network_disabled=True,
    )
    executor.execute(req)
    assert executor.last_run_config is not None
    assert executor.last_run_config.network_disabled is True
    assert executor.last_run_config.timeout_seconds == 45
    assert executor.last_run_config.memory_limit_mb == 512
    assert executor.last_run_config.cpu_limit == 0.5


def test_mock_success(executor_success: MockSandboxExecutor) -> None:
    resp = executor_success.execute(_req())
    assert resp.success is True
    assert resp.resource_killed is False
    assert resp.exit_code == 0
    assert len(resp.result_rows) >= 1
    assert "order_id" in resp.result_columns


def test_mock_syntax_error() -> None:
    executor = MockSandboxExecutor(MockScenario.SYNTAX_ERROR)
    resp = executor.execute(_req(op_content="SELEC broken"))
    assert resp.success is False
    assert resp.resource_killed is False
    assert resp.exit_code == 1
    assert "syntax" in resp.stderr.lower()


def test_mock_timeout() -> None:
    executor = MockSandboxExecutor(MockScenario.TIMEOUT)
    resp = executor.execute(_req(timeout_seconds=10))
    assert resp.success is False
    assert resp.resource_killed is True
    assert resp.exit_code == 124
    assert "timed out" in resp.stderr.lower()
    assert executor.last_run_config is not None
    assert executor.last_run_config.timeout_seconds == 10


def test_mock_resource_limit() -> None:
    executor = MockSandboxExecutor(MockScenario.RESOURCE_LIMIT)
    resp = executor.execute(_req(memory_limit_mb=128, cpu_limit=0.25))
    assert resp.success is False
    assert resp.resource_killed is True
    assert resp.exit_code == 137
    assert "memory_limit_mb=128" in resp.stderr
    assert "cpu_limit=0.25" in resp.stderr


def test_set_scenario_switches_outcome() -> None:
    executor = MockSandboxExecutor(MockScenario.SUCCESS)
    assert executor.execute(_req()).success is True
    executor.set_scenario(MockScenario.TIMEOUT)
    assert executor.execute(_req()).resource_killed is True


def test_mock_never_host_executes_op_content() -> None:
    """Guardrail: mock must not eval generated code (would raise if it did)."""
    bomb = "raise SystemExit('host executed mock op_content')"
    executor = MockSandboxExecutor(MockScenario.SUCCESS)
    resp = executor.execute(_req(op_type="python", op_content=bomb))
    assert resp.success is True
    # Only length metadata retained, never the bomb text.
    assert all(bomb not in item for item in executor.executed_op_contents)


def test_terminate_blocks_subsequent_execute() -> None:
    executor = MockSandboxExecutor(MockScenario.SUCCESS)
    executor.terminate("user_cancel")
    assert executor.terminated is True
    assert executor.terminate_reason == "user_cancel"
    resp = executor.execute(_req())
    assert resp.success is False
    assert "terminated" in resp.stderr.lower()


@pytest.fixture()
def executor_success() -> MockSandboxExecutor:
    return MockSandboxExecutor(MockScenario.SUCCESS)
