"""MCP 安全框架：本机信任用户模型下的按工具限流。"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from threading import Lock
from typing import Any, cast

from loguru import logger

from .errors import MCPToolError

DEFAULT_RATE_LIMIT = {"max_tokens": 100, "refill_rate": 10.0}
TOOL_RATE_LIMITS: dict[str, dict[str, float]] = {
    "get_cached_article": {"max_tokens": 200, "refill_rate": 20.0},
    "read_article": {"max_tokens": 200, "refill_rate": 20.0},
    "ingest_article": {"max_tokens": 20, "refill_rate": 2.0},
    "summarize_article": {"max_tokens": 10, "refill_rate": 1.0},
}


@dataclass
class RateLimiter:
    max_tokens: int = 100
    refill_rate: float = 10.0
    _tokens: float = field(default=100.0, init=False)
    _last_refill: float = field(default_factory=time.time, init=False)
    _lock: Lock = field(default_factory=Lock, init=False)

    def consume(self, tokens: int = 1) -> bool:
        with self._lock:
            now = time.time()
            elapsed = now - self._last_refill
            self._tokens = min(self.max_tokens, self._tokens + elapsed * self.refill_rate)
            self._last_refill = now
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def get_wait_time(self, tokens: int = 1) -> float:
        with self._lock:
            if self._tokens >= tokens:
                return 0.0
            needed = tokens - self._tokens
            if self.refill_rate <= 0:
                return float("inf")
            return needed / self.refill_rate


class SecurityManager:
    def __init__(
        self,
        enable_rate_limit: bool = True,
        rate_limit_config: dict[str, Any] | None = None,
    ) -> None:
        self.enable_rate_limit = enable_rate_limit
        self._rate_limit_config = rate_limit_config
        self.rate_limiters: dict[str, RateLimiter] = {}

    def _limiter_for(self, tool_name: str) -> RateLimiter:
        limiter = self.rate_limiters.get(tool_name)
        if limiter is None:
            config = self._rate_limit_config or TOOL_RATE_LIMITS.get(tool_name, DEFAULT_RATE_LIMIT)
            limiter = RateLimiter(
                max_tokens=int(config["max_tokens"]),
                refill_rate=float(config["refill_rate"]),
            )
            self.rate_limiters[tool_name] = limiter
        return limiter

    def check_rate_limit(self, tool_name: str, tokens: int = 1) -> tuple[bool, float]:
        if not self.enable_rate_limit:
            return True, 0.0
        limiter = self._limiter_for(tool_name)
        if limiter.consume(tokens):
            return True, 0.0
        return False, limiter.get_wait_time(tokens)


_security_manager: SecurityManager | None = None


def get_security_manager() -> SecurityManager:
    global _security_manager
    if _security_manager is None:
        _security_manager = SecurityManager()
    return _security_manager


def reset_security_manager() -> None:
    global _security_manager
    _security_manager = None


def secure_tool[F: Callable[..., Any]](func: F) -> F:
    """Rate-limit tools and re-raise MCPToolError so clients see isError."""

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        manager = get_security_manager()
        tool_name = func.__name__
        allowed, wait_time = manager.check_rate_limit(tool_name)
        if not allowed:
            raise MCPToolError("rate_limited", f"速率限制：请等待 {wait_time:.2f} 秒")
        try:
            return await func(*args, **kwargs)
        except MCPToolError:
            raise
        except Exception as exc:
            logger.exception("{} 未处理异常", tool_name)
            raise MCPToolError("tool_failed", "工具执行失败") from exc

    return cast(F, wrapper)
