"""MCP 限流与 secure_tool。"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from wechat_article_reader.mcp.errors import MCPToolError
from wechat_article_reader.mcp.security import (
    RateLimiter,
    SecurityManager,
    get_security_manager,
    reset_security_manager,
    secure_tool,
)


class TestRateLimiter:
    def test_consume_within_capacity(self):
        limiter = RateLimiter(max_tokens=10, refill_rate=1.0)
        assert limiter.consume(1) is True

    def test_consume_exceeds_capacity(self):
        limiter = RateLimiter(max_tokens=5, refill_rate=0.0)
        assert limiter.consume(6) is False

    def test_consume_drains_tokens(self):
        limiter = RateLimiter(max_tokens=3, refill_rate=0.0)
        assert limiter.consume(1) is True
        assert limiter.consume(1) is True
        assert limiter.consume(1) is True
        assert limiter.consume(1) is False

    def test_refill_restores_tokens(self, monkeypatch):
        now = 100.0
        monkeypatch.setattr("wechat_article_reader.mcp.security.time.time", lambda: now)
        limiter = RateLimiter(max_tokens=10, refill_rate=1000.0)
        limiter._last_refill = now
        limiter.consume(10)
        assert limiter.consume(1) is False
        now += 0.02
        assert limiter.consume(1) is True

    def test_get_wait_time_zero_when_available(self):
        limiter = RateLimiter(max_tokens=10, refill_rate=1.0)
        assert limiter.get_wait_time(1) == 0.0

    def test_get_wait_time_positive_when_empty(self):
        limiter = RateLimiter(max_tokens=1, refill_rate=10.0)
        limiter.consume(1)
        assert limiter.get_wait_time(1) > 0.0


class TestSecurityManager:
    def test_rate_limit_integration(self):
        mgr = SecurityManager(
            enable_rate_limit=True,
            rate_limit_config={"max_tokens": 2, "refill_rate": 0.0},
        )
        ok1, _ = mgr.check_rate_limit("tool_a")
        ok2, _ = mgr.check_rate_limit("tool_a")
        ok3, wait = mgr.check_rate_limit("tool_a")
        assert ok1 is True
        assert ok2 is True
        assert ok3 is False
        assert wait > 0.0

    def test_rate_limit_disabled(self):
        mgr = SecurityManager(enable_rate_limit=False)
        for _ in range(200):
            ok, _ = mgr.check_rate_limit("tool_a")
            assert ok is True

    def test_rate_limit_per_tool_isolation(self):
        mgr = SecurityManager(
            enable_rate_limit=True,
            rate_limit_config={"max_tokens": 1, "refill_rate": 0.0},
        )
        assert mgr.check_rate_limit("tool_a")[0] is True
        assert mgr.check_rate_limit("tool_b")[0] is True
        assert mgr.check_rate_limit("tool_a")[0] is False
        assert mgr.check_rate_limit("tool_b")[0] is False


class TestSecureTool:
    def setup_method(self):
        reset_security_manager()

    def teardown_method(self):
        reset_security_manager()

    def test_decorator_allows_execution(self):
        @secure_tool
        async def my_tool(url: str = "") -> dict:
            return {"success": True, "url": url}

        result = asyncio.run(my_tool(url="https://example.com"))
        assert result["success"] is True

    def test_decorator_rate_limited(self):
        mgr = SecurityManager(
            enable_rate_limit=True,
            rate_limit_config={"max_tokens": 1, "refill_rate": 0.0},
        )
        with patch("wechat_article_reader.mcp.security.get_security_manager", return_value=mgr):

            @secure_tool
            async def limited_tool() -> dict:
                return {"ok": True}

            assert asyncio.run(limited_tool())["ok"] is True
            with pytest.raises(MCPToolError) as err:
                asyncio.run(limited_tool())
            assert err.value.code == "rate_limited"

    def test_decorator_maps_exceptions_to_tool_failed(self):
        @secure_tool
        async def failing_tool() -> dict:
            raise ValueError("测试错误")

        with pytest.raises(MCPToolError) as err:
            asyncio.run(failing_tool())
        assert err.value.code == "tool_failed"
        assert "测试错误" not in str(err.value)


class TestSecurityManagerSingleton:
    def setup_method(self):
        reset_security_manager()

    def teardown_method(self):
        reset_security_manager()

    def test_get_returns_same_instance(self):
        assert get_security_manager() is get_security_manager()

    def test_reset_creates_new_instance(self):
        mgr1 = get_security_manager()
        reset_security_manager()
        assert get_security_manager() is not mgr1
