from __future__ import annotations

from inspect import signature

import pytest

from wechat_article_reader.mcp import server
from wechat_article_reader.mcp.input_validator import (
    MCPInputValidator,
    MCPValidationError,
    export_root_context,
)
from wechat_article_reader.shared.constants import DEFAULT_MCP_HTTP_PORT
from wechat_article_reader.shared.exceptions import InfrastructureError


def test_register_tools_wires_only_article_and_reading_toolsets(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(server, "register_article_tools", lambda _: calls.append("article"))
    monkeypatch.setattr(server, "register_reading_tools", lambda _: calls.append("reading"))
    server._register_tools(object())
    assert calls == ["article", "reading"]


def test_ensure_mcp_registers_no_resources(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(server, "_get_mcp", lambda: object())
    monkeypatch.setattr(server, "_register_tools", lambda _: calls.append("tools"))
    monkeypatch.setattr(server, "mcp", None)
    assert server._ensure_mcp() is not None
    assert calls == ["tools"]


def test_http_app_uses_streamable_http(monkeypatch) -> None:
    from starlette.applications import Starlette

    calls: list[str] = []

    class _FakeMcp:
        def streamable_http_app(self) -> Starlette:
            calls.append("streamable")
            return Starlette()

        def sse_app(self) -> Starlette:
            calls.append("sse")
            return Starlette()

    app = server.build_http_app(_FakeMcp())
    assert calls == ["streamable"]
    assert app is not None


def test_mcp_http_default_port_does_not_conflict_with_web() -> None:
    assert signature(server.run_mcp_server).parameters["port"].default == DEFAULT_MCP_HTTP_PORT
    assert DEFAULT_MCP_HTTP_PORT == 8001


def test_remote_http_requires_authentication(monkeypatch) -> None:
    monkeypatch.setattr(server, "_ensure_mcp", lambda: object())
    with pytest.raises(InfrastructureError, match="鉴权令牌"):
        server.run_mcp_server(transport="http", host="0.0.0.0", allow_remote=True)


def test_remote_export_path_stays_under_output_root(tmp_path) -> None:
    root = tmp_path / "output"
    root.mkdir()
    token = export_root_context.set(root.resolve())
    try:
        assert MCPInputValidator.validate_export_path(str(root / "article.md")) == str(
            (root / "article.md").resolve()
        )
        with pytest.raises(MCPValidationError, match="configured export directory"):
            MCPInputValidator.validate_export_path(str(tmp_path / "outside.md"))
    finally:
        export_root_context.reset(token)
