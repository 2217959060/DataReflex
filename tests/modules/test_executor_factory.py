# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from data_reflex.config.settings import Settings
from data_reflex.modules.executor.docker_executor import DockerSandboxExecutor
from data_reflex.modules.executor.factory import make_executor_factory
from data_reflex.modules.executor.mock_executor import MockSandboxExecutor


def test_factory_defaults_to_mock(tmp_path: Path) -> None:
    settings = Settings(
        sandbox_backend="mock",
        sqlite_path=str(tmp_path / "t.db"),
    )
    factory = make_executor_factory(settings, dataset_root=tmp_path / "datasets")
    assert isinstance(factory(), MockSandboxExecutor)


def test_factory_docker_backend(tmp_path: Path) -> None:
    settings = Settings(
        sandbox_backend="docker",
        sqlite_path=str(tmp_path / "t.db"),
        sandbox_image="data-reflex-sandbox:3.11",
    )
    factory = make_executor_factory(settings, dataset_root=tmp_path / "datasets")
    assert isinstance(factory(), DockerSandboxExecutor)


def test_sql_runner_loads_csv_tables(tmp_path: Path) -> None:
    from data_reflex.modules.executor.docker_executor import _sql_runner_source

    src = _sql_runner_source()
    assert "/sandbox/data" in src
    assert "glob('*.csv')" in src
    assert "print(" not in src
