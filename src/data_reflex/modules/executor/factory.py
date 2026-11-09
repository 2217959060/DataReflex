# -*- coding: utf-8 -*-
"""按 settings 选择沙箱后端：mock（罐头行）或 docker（真容器）。

上游：应用启动 / Queue 组装节点时取执行器工厂。
本文件：读 ``SANDBOX_BACKEND``，返回零参工厂 ``() -> SandboxExecutor``。
下游：``Node_Execute`` 注入具体执行器。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from data_reflex.config.settings import Settings, get_settings
from data_reflex.modules.executor.base import SandboxExecutor
from data_reflex.modules.executor.docker_executor import DockerSandboxExecutor
from data_reflex.modules.executor.mock_executor import MockSandboxExecutor, MockScenario

ExecutorFactory = Callable[[], SandboxExecutor]


def make_executor_factory(
    settings: Settings | None = None,
    *,
    dataset_root: Path | str | None = None,
) -> ExecutorFactory:
    """返回已配置后端的零参工厂（每次调用新建执行器实例）。"""
    cfg = settings or get_settings()
    root = (
        Path(dataset_root)
        if dataset_root is not None
        else Path(cfg.sqlite_path).parent / "datasets"
    )

    if cfg.sandbox_backend == "docker":

        def _docker() -> SandboxExecutor:
            return DockerSandboxExecutor(settings=cfg, dataset_root=root)

        return _docker

    def _mock() -> SandboxExecutor:
        return MockSandboxExecutor(MockScenario.SUCCESS)

    return _mock
