# -*- coding: utf-8 -*-
"""FastAPI 依赖：进程级 ``AppRuntime``（settings / 队列 / 导入 / 审计等一次装配）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import Request

from data_reflex.api.queue import ExecutorFactory, TaskQueue
from data_reflex.config.settings import Settings, get_settings
from data_reflex.modules.audit.store import AuditStore
from data_reflex.modules.data_import.importer import DataImporter
from data_reflex.modules.executor.factory import make_executor_factory
from data_reflex.modules.human_confirm.gate import PlanConfirmGate


@dataclass
class AppRuntime:
    """启动时装配一次的进程级服务容器，供路由 Depends 注入。"""

    settings: Settings
    audit_store: AuditStore
    importer: DataImporter
    queue: TaskQueue
    confirm_gate: PlanConfirmGate
    upload_dir: Path


def build_runtime(
    *,
    settings: Settings | None = None,
    dataset_root: Path | str | None = None,
    sqlite_path: Path | str | None = None,
    executor_factory: ExecutorFactory | None = None,
    upload_dir: Path | str | None = None,
) -> AppRuntime:
    cfg = settings or get_settings()
    db_path = Path(sqlite_path) if sqlite_path is not None else Path(cfg.sqlite_path)
    audit = AuditStore(db_path)
    root = (
        Path(dataset_root) if dataset_root is not None else db_path.parent / "datasets"
    )
    importer = DataImporter(settings=cfg, dataset_root=root)
    gate = PlanConfirmGate(timeout_seconds=cfg.plan_confirm_timeout_seconds)
    factory = executor_factory or make_executor_factory(cfg, dataset_root=root)
    queue = TaskQueue(
        audit_store=audit,
        confirm_gate=gate,
        executor_factory=factory,
    )
    uploads = (
        Path(upload_dir) if upload_dir is not None else db_path.parent / "_uploads"
    )
    uploads.mkdir(parents=True, exist_ok=True)
    return AppRuntime(
        settings=cfg,
        audit_store=audit,
        importer=importer,
        queue=queue,
        confirm_gate=gate,
        upload_dir=uploads,
    )


def get_runtime(request: Request) -> AppRuntime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise RuntimeError("AppRuntime is not configured on app.state")
    assert isinstance(runtime, AppRuntime)
    return runtime
