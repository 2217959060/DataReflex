# -*- coding: utf-8 -*-
"""T37: safety + sandbox integration (Mock / fake Docker; no host code exec)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from data_reflex.config.settings import Settings
from data_reflex.exceptions import SandboxUnavailableError
from data_reflex.graph_engine.builder import (
    build_main_graph,
    default_nodes,
    make_thread_config,
    open_sqlite_checkpointer,
)
from data_reflex.graph_engine.nodes.base import BaseNode
from data_reflex.graph_engine.nodes.execute import NodeExecute
from data_reflex.models.schemas import ExecuteReq, ExecuteResp, GenerateOpResp
from data_reflex.models.state import StrategyFlags, create_initial_task_state
from data_reflex.modules.executor.docker_executor import DockerSandboxExecutor
from data_reflex.modules.executor.mock_executor import MockSandboxExecutor, MockScenario
from data_reflex.utils.tracing import create_tracer, reset_tracer

_QUERY = "对比 APP 和官网 渠道销售额"
_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "data_reflex"


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


def _seed(*, task_id: str, flags: StrategyFlags | None = None) -> dict[str, Any]:
    return dict(
        create_initial_task_state(
            dataset_id="ds-safe",
            user_query=_QUERY,
            task_id=task_id,
            strategy_flags=flags
            or StrategyFlags(
                enable_human_confirm=False,
                enable_fallback_report=True,
            ),
        )
    )


class _ForcedOpGenerate(BaseNode):
    """Inject a fixed op so SafetyCheck / Execute see controlled content."""

    name = "Node_GenerateOp"

    def __init__(self, op: GenerateOpResp) -> None:
        super().__init__()
        self._op = op

    def execute(self, state: Any) -> dict[str, Any]:
        return {
            "generated_ops": {"st-forced": self._op},
            "current_op": self._op,
            "current_sub_task_id": "st-forced",
            "next_node": "safety_check",
        }


class _CountingExecutor:
    """Records execute calls — must stay 0 when safety blocks."""

    def __init__(self) -> None:
        self.calls = 0
        self._inner = MockSandboxExecutor(MockScenario.SUCCESS)

    def execute(self, req: ExecuteReq) -> ExecuteResp:
        self.calls += 1
        return self._inner.execute(req)


class _FakeContainer:
    def __init__(
        self,
        *,
        status: int = 0,
        logs: bytes = b'__DATA_REFLEX_RESULT__{"columns":["n"],"rows":[{"n":1}]}',
        wait_exc: Exception | None = None,
    ) -> None:
        self.id = "cid-int"
        self._status = status
        self._logs = logs
        self._wait_exc = wait_exc
        self.killed = False
        self.removed = False

    def wait(self, timeout: int | None = None) -> dict[str, int]:
        if self._wait_exc is not None:
            raise self._wait_exc
        return {"StatusCode": self._status}

    def logs(self, stdout: bool = True, stderr: bool = True) -> bytes:
        return self._logs

    def kill(self) -> None:
        self.killed = True

    def remove(self, force: bool = False) -> None:
        self.removed = True


def _mock_client(container: _FakeContainer | None = None) -> MagicMock:
    client = MagicMock()
    client.ping.return_value = True
    client.images.get.return_value = MagicMock()
    client.containers.run.return_value = container or _FakeContainer()
    return client


def _settings(**kwargs: object) -> Settings:
    defaults: dict[str, object] = {
        "sandbox_image": "data-reflex-sandbox:3.11",
        "enable_sandbox": True,
        "sqlite_path": "data/data_reflex.db",
    }
    defaults.update(kwargs)
    return Settings.model_validate(defaults)


# ---------------------------------------------------------------------------
# Dangerous ops must be blocked before execute
# ---------------------------------------------------------------------------


def test_dangerous_sql_blocked_no_execute() -> None:
    op = GenerateOpResp(
        op_type="sql",
        op_content="DELETE FROM orders WHERE channel = 'APP'",
        format_valid=True,
    )
    counter = _CountingExecutor()
    nodes = default_nodes(
        generate_op=_ForcedOpGenerate(op),
        execute=NodeExecute(executor=counter),
    )
    with open_sqlite_checkpointer(":memory:") as cp:
        out = build_main_graph(nodes=nodes, checkpointer=cp).invoke(
            _seed(task_id="safe-sql"),
            config=make_thread_config("safe-sql"),
        )
    assert counter.calls == 0
    safety = out.get("safety_check")
    assert safety is not None
    assert safety.passed is False
    assert safety.violation_type == "dangerous_write"
    assert out.get("execute_resp") is None or not getattr(
        out.get("execute_resp"), "success", False
    )


def test_dangerous_python_blocked_no_execute() -> None:
    op = GenerateOpResp(
        op_type="python",
        op_content="import os\nos.system('rm -rf /')\n",
        format_valid=True,
    )
    counter = _CountingExecutor()
    nodes = default_nodes(
        generate_op=_ForcedOpGenerate(op),
        execute=NodeExecute(executor=counter),
    )
    with open_sqlite_checkpointer(":memory:") as cp:
        out = build_main_graph(nodes=nodes, checkpointer=cp).invoke(
            _seed(task_id="safe-py"),
            config=make_thread_config("safe-py"),
        )
    assert counter.calls == 0
    assert out["safety_check"].passed is False
    assert out["safety_check"].violation_type == "dangerous_write"


def test_unauthorized_column_blocked_no_execute() -> None:
    op = GenerateOpResp(
        op_type="sql",
        op_content="SELECT order_id, phone FROM orders",
        format_valid=True,
    )
    counter = _CountingExecutor()
    nodes = default_nodes(
        generate_op=_ForcedOpGenerate(op),
        execute=NodeExecute(executor=counter),
    )
    with open_sqlite_checkpointer(":memory:") as cp:
        out = build_main_graph(nodes=nodes, checkpointer=cp).invoke(
            _seed(task_id="safe-col"),
            config=make_thread_config("safe-col"),
        )
    assert counter.calls == 0
    assert out["safety_check"].passed is False
    assert out["safety_check"].violation_type == "unauthorized_column"


# ---------------------------------------------------------------------------
# Sandbox timeout / resource / unavailable
# ---------------------------------------------------------------------------


def test_sandbox_timeout_marks_resource_killed_via_graph() -> None:
    nodes = default_nodes(
        execute=NodeExecute(executor=MockSandboxExecutor(MockScenario.TIMEOUT))
    )
    with open_sqlite_checkpointer(":memory:") as cp:
        out = build_main_graph(nodes=nodes, checkpointer=cp).invoke(
            _seed(task_id="sbx-timeout"),
            config=make_thread_config("sbx-timeout"),
        )
    resp = out.get("execute_resp")
    assert resp is not None
    assert resp.success is False
    assert resp.resource_killed is True
    assert any(r.status == "failed" for r in (out.get("sub_task_results") or []))
    assert out.get("final_report") is not None


def test_resource_limit_marks_failed_via_graph() -> None:
    nodes = default_nodes(
        execute=NodeExecute(executor=MockSandboxExecutor(MockScenario.RESOURCE_LIMIT))
    )
    with open_sqlite_checkpointer(":memory:") as cp:
        out = build_main_graph(nodes=nodes, checkpointer=cp).invoke(
            _seed(task_id="sbx-mem"),
            config=make_thread_config("sbx-mem"),
        )
    assert out["execute_resp"].resource_killed is True
    assert out.get("final_report") is not None


def test_docker_unavailable_raises_no_host_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = DockerSandboxExecutor(
        settings=_settings(),
        dataset_root=tmp_path / "datasets",
        client=None,
    )

    def _boom() -> Any:
        raise OSError("no docker")

    import docker as docker_mod

    monkeypatch.setattr(docker_mod, "from_env", _boom)

    nodes = default_nodes(execute=NodeExecute(executor=exe))
    with open_sqlite_checkpointer(":memory:") as cp:
        app = build_main_graph(nodes=nodes, checkpointer=cp)
        with pytest.raises(SandboxUnavailableError):
            app.invoke(
                _seed(task_id="sbx-down"),
                config=make_thread_config("sbx-down"),
            )


def test_docker_timeout_kills_oneshot_container(tmp_path: Path) -> None:
    container = _FakeContainer(wait_exc=TimeoutError("Read timed out"))
    client = _mock_client(container)
    exe = DockerSandboxExecutor(
        settings=_settings(),
        dataset_root=tmp_path / "datasets",
        client=client,
    )
    # Safe SELECT through Execute node only (bypass GenerateOp).
    node = NodeExecute(executor=exe)
    state: dict[str, Any] = dict(
        create_initial_task_state(
            dataset_id="ds-1",
            user_query=_QUERY,
            task_id="t-to",
            strategy_flags=StrategyFlags(enable_human_confirm=False),
        )
    )
    state["current_op"] = GenerateOpResp(
        op_type="sql",
        op_content="SELECT channel FROM orders",
        format_valid=True,
    )
    state["current_sub_task_id"] = "st-1"
    delta = node.run(state)  # type: ignore[arg-type]
    assert delta["execute_resp"].resource_killed is True
    assert container.killed is True
    assert container.removed is True


def test_enable_sandbox_false_still_oneshot_container(tmp_path: Path) -> None:
    client = _mock_client()
    exe = DockerSandboxExecutor(
        settings=_settings(enable_sandbox=False),
        dataset_root=tmp_path / "datasets",
        client=client,
        network_disabled=True,
    )
    nodes = default_nodes(
        execute=NodeExecute(executor=exe, settings=_settings(enable_sandbox=False))
    )
    with open_sqlite_checkpointer(":memory:") as cp:
        out = build_main_graph(nodes=nodes, checkpointer=cp).invoke(
            _seed(
                task_id="sbx-off",
                flags=StrategyFlags(
                    enable_sandbox=False,
                    enable_human_confirm=False,
                ),
            ),
            config=make_thread_config("sbx-off"),
        )
    assert client.containers.run.called
    kwargs = exe.last_run_kwargs
    assert kwargs is not None
    assert kwargs["detach"] is True
    assert kwargs["network_disabled"] is True
    assert client.containers.run.return_value.removed is True
    assert out.get("final_report") is not None


# ---------------------------------------------------------------------------
# Host must never eval/exec generated op content
# ---------------------------------------------------------------------------


def _assert_no_host_eval_exec(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    banned = {"eval", "exec"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in banned, f"{path} calls {node.func.id}"


def test_no_host_exec_paths_in_executor_and_nodes() -> None:
    targets = [
        _SRC_ROOT / "modules" / "executor" / "docker_executor.py",
        _SRC_ROOT / "modules" / "executor" / "mock_executor.py",
        _SRC_ROOT / "graph_engine" / "nodes" / "execute.py",
        _SRC_ROOT / "modules" / "safety_check" / "checker.py",
    ]
    for path in targets:
        assert path.is_file(), path
        _assert_no_host_eval_exec(path)


def test_mock_executor_never_runs_bomb_payload() -> None:
    bomb = "raise SystemExit('host executed op_content')"
    exe = MockSandboxExecutor(MockScenario.SUCCESS)
    resp = exe.execute(
        ExecuteReq(
            op_type="python",
            op_content=bomb,
            dataset_id="ds-1",
            timeout_seconds=10,
            memory_limit_mb=256,
            cpu_limit=0.5,
        )
    )
    assert resp.success is True
    # Only metadata recorded — not raw bomb text.
    assert bomb not in "".join(exe.executed_op_contents)
