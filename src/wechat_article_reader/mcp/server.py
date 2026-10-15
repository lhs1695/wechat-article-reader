"""MCP server composition root.

This module intentionally stays thin:

- it creates the FastMCP instance
- it wires composable toolsets and resources
- it adapts transport/runtime concerns such as HTTP auth

Business behavior should live in dedicated feature slices and tool/resource
modules instead of being inlined here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ..shared.constants import DEFAULT_MCP_HTTP_PORT
from ..shared.encoding import configure_utf8_stdio
from ..shared.exceptions import InfrastructureError
from .toolsets import register_article_tools, register_reading_tools

if TYPE_CHECKING:
    from starlette.applications import Starlette


_mcp_available = True
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    _mcp_available = False
    FastMCP = None  # type: ignore[assignment,misc]


mcp: FastMCP | None = None


def _get_mcp() -> FastMCP:
    """Create the FastMCP application instance."""
    if not _mcp_available:
        raise ImportError("MCP SDK 未安装。请运行: pip install 'mcp>=1.28,<2'")
    return FastMCP("WeChat Article Reader")


def _register_tools(mcp_instance: FastMCP) -> None:
    """Register all MCP toolsets."""
    register_article_tools(mcp_instance)
    register_reading_tools(mcp_instance)


def _ensure_mcp() -> FastMCP:
    """Lazily build the MCP application once."""
    global mcp
    if mcp is None:
        mcp = _get_mcp()
        _register_tools(mcp)
    return mcp


def _streamable_http_app(mcp_instance: FastMCP) -> Starlette:
    """Return FastMCP's Streamable HTTP ASGI app."""
    from starlette.applications import Starlette as StarletteApp

    factory = getattr(mcp_instance, "streamable_http_app", None) or getattr(
        mcp_instance, "http_app", None
    )
    if not callable(factory):
        raise InfrastructureError("当前 MCP SDK 不支持 Streamable HTTP")
    app = factory()
    if not isinstance(app, StarletteApp):
        raise InfrastructureError("当前 MCP SDK 不支持 Streamable HTTP")
    return app


def build_http_app(
    mcp_instance: FastMCP,
    auth_token: str | None = None,
    remote_export_root: str | None = None,
) -> Starlette:
    """Build the HTTP transport app with optional token authentication."""
    from starlette.responses import JSONResponse

    from .input_validator import export_root_context

    class TokenAuthMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            if auth_token:
                headers = dict(scope.get("headers", ()))
                request_token = headers.get(b"x-mcp-token", b"").decode("utf-8", errors="replace")
                if request_token != auth_token:
                    response = JSONResponse(
                        {"success": False, "error": "Unauthorized"}, status_code=401
                    )
                    return await response(scope, receive, send)
            token = export_root_context.set(
                None if remote_export_root is None else Path(remote_export_root).resolve()
            )
            try:
                return await self.app(scope, receive, send)
            finally:
                export_root_context.reset(token)

    inner = _streamable_http_app(mcp_instance)
    inner.add_middleware(TokenAuthMiddleware)
    return inner


def run_mcp_server(
    transport: str = "stdio",
    port: int = DEFAULT_MCP_HTTP_PORT,
    host: str = "127.0.0.1",
    auth_token: str | None = None,
    allow_remote: bool = False,
) -> None:
    """Run the MCP server."""
    configure_utf8_stdio()
    mcp_instance = _ensure_mcp()
    logger.info(f"启动 MCP 服务器 (transport={transport})")

    if transport == "stdio":
        mcp_instance.run(transport="stdio")
        return

    if transport != "http":
        raise InfrastructureError(f"不支持的传输方式: {transport}")

    is_remote_host = host not in {"127.0.0.1", "localhost", "::1"}
    if is_remote_host and not allow_remote:
        raise InfrastructureError("远程监听已被禁止。若确需远程访问，请显式传入 --allow-remote。")

    if is_remote_host:
        if not auth_token:
            raise InfrastructureError("远程监听必须配置 MCP 鉴权令牌")
        logger.warning("MCP HTTP 正在远程监听，请确保网络隔离与鉴权配置。")

    import uvicorn

    remote_export_root = None
    if is_remote_host:
        from ..infrastructure.config import get_settings

        remote_export_root = str(Path(get_settings().export.default_output_dir).resolve())
    app = build_http_app(mcp_instance, auth_token=auth_token, remote_export_root=remote_export_root)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_mcp_server()
