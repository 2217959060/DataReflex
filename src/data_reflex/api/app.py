# -*- coding: utf-8 -*-
"""FastAPI 应用工厂：挂路由、CORS、异常处理与启动时 runtime。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from data_reflex.api.deps import AppRuntime, build_runtime
from data_reflex.api.errors import register_exception_handlers
from data_reflex.api.queue import ExecutorFactory
from data_reflex.api.routes import datasets, tasks
from data_reflex.config.settings import Settings


def create_app(
    *,
    settings: Settings | None = None,
    dataset_root: Path | str | None = None,
    sqlite_path: Path | str | None = None,
    executor_factory: ExecutorFactory | None = None,
    upload_dir: Path | str | None = None,
    runtime: AppRuntime | None = None,
) -> FastAPI:
    """Build the DataReflex API app (injectable for tests)."""
    app = FastAPI(title="DataReflex", version="0.1.0")
    # Vite dev (5173) may call API directly; production often same-origin via proxy.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    rt = runtime or build_runtime(
        settings=settings,
        dataset_root=dataset_root,
        sqlite_path=sqlite_path,
        executor_factory=executor_factory,
        upload_dir=upload_dir,
    )
    app.state.runtime = rt

    app.include_router(datasets.router)
    app.include_router(tasks.router)

    @app.get("/health")
    def health() -> dict[str, Any]:
        # V2-9 sandbox mode; V2-7 tracing_enabled (Langfuse keys present).
        from data_reflex.utils.tracing import is_langfuse_configured

        return {
            "status": "ok",
            "sandbox_backend": rt.settings.sandbox_backend,
            "tracing_enabled": is_langfuse_configured(
                public_key=rt.settings.langfuse_public_key,
                secret_key=rt.settings.langfuse_secret_key,
            ),
        }

    @app.on_event("shutdown")
    def _shutdown() -> None:
        rt.queue.shutdown()
        rt.audit_store.close()
        try:
            from data_reflex.utils.tracing import get_tracer

            get_tracer().shutdown()
        except Exception:  # noqa: BLE001 — shutdown must not raise
            pass

    return app
