from __future__ import annotations

from inspect import signature

from wechat_article_reader.mcp import server
from wechat_article_reader.shared.constants import DEFAULT_MCP_HTTP_PORT


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
