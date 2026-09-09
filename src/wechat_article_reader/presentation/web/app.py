"""Web 应用入口 — FastAPI 应用工厂和启动逻辑"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from ...infrastructure.adapters.http_client_pool import get_http_pool
from ...infrastructure.config import Container, get_container, reset_container
from ...shared.exceptions import ErrorCode
from .api.endpoints import router as api_router
from .api.models import ErrorResponse
from .errors import map_exception, validation_error_payload
from .routes.pages import router as pages_router

STATIC_DIR = Path(__file__).parent / "static"


def _prewarm_components(container: Container) -> None:
    _ = (container.summarizers, container.exporters, container.scrapers)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    container: Container = app.state.container
    prewarm_task: asyncio.Task[None] | None = None
    if app.state.owns_container:
        from ...infrastructure.persistence import upgrade_database

        await asyncio.to_thread(upgrade_database)
    if app.state.prewarm_enabled:
        prewarm_task = asyncio.create_task(asyncio.to_thread(_prewarm_components, container))
    try:
        yield
    finally:
        if prewarm_task is not None:
            try:
                await prewarm_task
            except Exception:
                logger.exception("Web 组件预热失败")
        await container.async_close()
        await get_http_pool().close_all()
        if app.state.owns_container:
            reset_container()


def create_app(
    container: Container | None = None,
    *,
    prewarm: bool = True,
) -> FastAPI:
    error_responses = {
        status_code: {"model": ErrorResponse} for status_code in (400, 404, 422, 500, 502, 503, 504)
    }
    app = FastAPI(
        title="WeChat Article Reader",
        lifespan=_lifespan,
        responses=cast(dict[int | str, dict[str, Any]], error_responses),
    )
    app.state.container = container or get_container()
    app.state.owns_container = container is None
    app.state.prewarm_enabled = prewarm

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        request.state.request_id = request_id
        request.state.csp_nonce = secrets.token_urlsafe(18)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path not in {"/docs", "/redoc", "/openapi.json"}:
            nonce = request.state.csp_nonce
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                f"script-src 'self' 'nonce-{nonce}'; "
                "script-src-attr 'none'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https:; connect-src 'self'; object-src 'none'; "
                "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
            )
        return response

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        _exc: RequestValidationError,
    ) -> JSONResponse:
        request_id = request.state.request_id
        return JSONResponse(
            status_code=422,
            content=validation_error_payload(request_id, _exc.errors()),
            headers={"X-Request-ID": request_id},
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
        request_id = request.state.request_id
        error_code = (
            ErrorCode.ARTICLE_NOT_FOUND.code
            if exc.status_code == 404
            else ErrorCode.INVALID_INPUT.code
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": str(exc.detail),
                "error_code": error_code,
                "request_id": request_id,
            },
            headers={"X-Request-ID": request_id},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = request.state.request_id
        status_code, payload = map_exception(exc, request_id)
        logger.exception("Web request failed request_id={}", request_id)
        return JSONResponse(
            status_code=status_code,
            content=payload,
            headers={"X-Request-ID": request_id},
        )

    app.include_router(pages_router)
    app.include_router(api_router, prefix="/api")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    log_host = "localhost" if host in ("0.0.0.0", "::") else host
    print(f"启动成功 → http://{log_host}:{port}")
    print(f"API 文档 → http://{log_host}:{port}/docs")
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
