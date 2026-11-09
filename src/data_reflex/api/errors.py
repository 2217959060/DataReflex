# -*- coding: utf-8 -*-
"""统一 API 错误响应与异常处理器注册。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from data_reflex.exceptions import DataReflexError


def error_body(
    *,
    code: str,
    message: str,
    detail: Any = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if detail is not None:
        payload["detail"] = detail
    return {"error": payload}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exc_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        detail = exc.detail
        message = detail if isinstance(detail, str) else "request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(
                code=f"http_{exc.status_code}", message=message, detail=detail
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                code="validation_error",
                message="request validation failed",
                detail=exc.errors(),
            ),
        )

    @app.exception_handler(DataReflexError)
    async def business_handler(_request: Request, exc: DataReflexError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=error_body(
                code=type(exc).__name__,
                message=str(exc) or type(exc).__name__,
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=error_body(
                code="internal_error",
                message=f"{type(exc).__name__}: {exc}",
            ),
        )
