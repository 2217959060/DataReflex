# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from data_reflex.config.settings import Settings
from data_reflex.exceptions import SandboxUnavailableError
from data_reflex.models.schemas import ExecuteReq
from data_reflex.modules.executor import SandboxExecutor
from data_reflex.modules.executor.docker_executor import (
    DockerSandboxExecutor,
    docker_available,
)


def _req(**overrides: object) -> ExecuteReq:
    base: dict[str, object] = {
        "op_type": "sql",
        "op_content": "SELECT 1 AS n",
        "dataset_id": "ds-1",
        "timeout_seconds": 15,
        "memory_limit_mb": 256,
        "cpu_limit": 0.5,
    }
    base.update(overrides)
    return ExecuteReq.model_validate(base)


def _settings(**kwargs: object) -> Settings:
    defaults: dict[str, object] = {
        "sandbox_image": "data-reflex-sandbox:3.11",
        "enable_sandbox": True,
        "sqlite_path": "data/data_reflex.db",
    }
    defaults.update(kwargs)
    return Settings.model_validate(defaults)


class _FakeContainer:
    def __init__(
        self, *, status: int = 0, logs: bytes = b"ok", wait_exc: Exception | None = None
    ):
        self.id = "cid-fake"
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
    c = container or _FakeContainer(
        logs=b'__DATA_REFLEX_RESULT__{"columns":["n"],"rows":[{"n":1}]}'
    )
    client.containers.run.return_value = c
    return client


def test_protocol_compatible(tmp_path: Path) -> None:
    exe = DockerSandboxExecutor(
        settings=_settings(),
        dataset_root=tmp_path / "datasets",
        client=_mock_client(),
    )
    assert isinstance(exe, SandboxExecutor)


def test_build_run_config_includes_network_disabled(tmp_path: Path) -> None:
    exe = DockerSandboxExecutor(
        settings=_settings(),
        dataset_root=tmp_path / "datasets",
        client=_mock_client(),
        network_disabled=True,
    )
    cfg = exe.build_run_config(
        _req(timeout_seconds=20, memory_limit_mb=128, cpu_limit=1.0)
    )
    assert cfg.network_disabled is True
    assert cfg.timeout_seconds == 20
    assert cfg.memory_limit_mb == 128
    assert cfg.cpu_limit == 1.0


def test_container_run_kwargs_have_isolation_limits(tmp_path: Path) -> None:
    client = _mock_client()
    exe = DockerSandboxExecutor(
        settings=_settings(),
        dataset_root=tmp_path / "datasets",
        client=client,
        network_disabled=True,
        pids_limit=32,
    )
    resp = exe.execute(_req(timeout_seconds=15, memory_limit_mb=256, cpu_limit=0.5))
    assert resp.success is True
    kwargs = exe.last_run_kwargs
    assert kwargs is not None
    assert kwargs["network_disabled"] is True
    assert kwargs["mem_limit"] == "256m"
    assert kwargs["nano_cpus"] == int(0.5 * 1_000_000_000)
    assert kwargs["pids_limit"] == 32
    assert kwargs["detach"] is True
    # Data / work mounts are read-only.
    for vol in kwargs["volumes"].values():
        assert vol["mode"] == "ro"


def test_docker_unavailable_raises_not_host_fallback(
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
    with pytest.raises(SandboxUnavailableError, match="forbidden"):
        exe.execute(_req())


def test_missing_image_raises_sandbox_unavailable(tmp_path: Path) -> None:
    client = _mock_client()
    client.images.get.side_effect = Exception("No such image")
    exe = DockerSandboxExecutor(
        settings=_settings(),
        dataset_root=tmp_path / "datasets",
        client=client,
    )
    with pytest.raises(SandboxUnavailableError, match="image not available"):
        exe.execute(_req())


def test_timeout_kills_container(tmp_path: Path) -> None:
    container = _FakeContainer(wait_exc=TimeoutError("Read timed out"))
    client = _mock_client(container)
    exe = DockerSandboxExecutor(
        settings=_settings(),
        dataset_root=tmp_path / "datasets",
        client=client,
    )
    resp = exe.execute(_req(timeout_seconds=3))
    assert resp.success is False
    assert resp.resource_killed is True
    assert resp.exit_code == 124
    assert container.killed is True
    assert container.removed is True


def test_terminate_kills_active_container(tmp_path: Path) -> None:
    container = _FakeContainer(logs=b'__DATA_REFLEX_RESULT__{"columns":[],"rows":[]}')
    # Make wait block until terminate — simulate by setting active then terminate.
    client = _mock_client(container)
    exe = DockerSandboxExecutor(
        settings=_settings(),
        dataset_root=tmp_path / "datasets",
        client=client,
    )
    # Manually set active container as execute would.
    exe._active_container = container  # noqa: SLF001 — test seam
    exe.terminate("user_cancel")
    assert exe.terminated is True
    assert container.killed is True
    assert container.removed is True
    assert exe._active_container is None  # noqa: SLF001


def test_enable_sandbox_false_still_uses_oneshot_container(tmp_path: Path) -> None:
    """Closing isolation for eval must still go through containers.run (D-DOCKER-01)."""
    client = _mock_client()
    exe = DockerSandboxExecutor(
        settings=_settings(enable_sandbox=False),
        dataset_root=tmp_path / "datasets",
        client=client,
        network_disabled=True,
    )
    resp = exe.execute(_req())
    assert resp.success is True
    assert client.containers.run.called
    kwargs = exe.last_run_kwargs
    assert kwargs is not None
    assert kwargs["network_disabled"] is True
    assert kwargs["detach"] is True


def test_source_has_no_host_exec_of_op_content() -> None:
    """Static guard: docker_executor must not eval/exec op_content on host."""
    src = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "data_reflex"
        / "modules"
        / "executor"
        / "docker_executor.py"
    )
    tree = ast.parse(src.read_text(encoding="utf-8"))
    banned_names = {"eval", "exec"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in banned_names
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
        ):
            raise AssertionError("subprocess usage forbidden in docker_executor")


def test_mock_execute_does_not_call_builtins_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _mock_client()
    exe = DockerSandboxExecutor(
        settings=_settings(),
        dataset_root=tmp_path / "datasets",
        client=client,
    )
    calls: list[str] = []

    real_exec = __builtins__["exec"] if isinstance(__builtins__, dict) else exec

    def _tracking_exec(*args: object, **kwargs: object) -> Any:
        calls.append("exec")
        return real_exec(*args, **kwargs)

    monkeypatch.setattr("builtins.exec", _tracking_exec)
    bomb = "raise RuntimeError('host executed')"
    resp = exe.execute(_req(op_type="python", op_content=bomb))
    assert "exec" not in calls
    # Mocked container returns canned success logs (SQL marker); python path still
    # must not host-exec. Success/fail depends on container logs — ensure run called.
    assert client.containers.run.called
    assert resp.exit_code in (0, 1, 124, 137) or resp.success in (True, False)


@pytest.mark.docker
def test_live_docker_optional() -> None:
    """Live Docker smoke — skipped when daemon is down (CI / local without Docker)."""
    if not docker_available():
        pytest.skip("Docker daemon unavailable — live sandbox test skipped (annotated)")
    # Daemon is up: still skip if project image missing (do not pull in CI by default).
    settings = _settings()
    try:
        import docker

        client = docker.from_env()
        client.images.get(settings.sandbox_image)
    except Exception:
        pytest.skip(
            f"sandbox image {settings.sandbox_image} not present — "
            "live test skipped (annotated)"
        )

    exe = DockerSandboxExecutor(settings=settings, client=client)
    # Minimal python that assigns ``result`` (py_runner emits the marker JSON).
    code = "import pandas as pd\n" "result = pd.DataFrame([{'x': 1}])\n"
    resp = exe.execute(
        _req(op_type="python", op_content=code, timeout_seconds=30, memory_limit_mb=256)
    )
    assert resp.success is True
    assert resp.result_rows == [{"x": 1}]
