"""Map domain and application exceptions to MCPToolError."""

from __future__ import annotations

from typing import NoReturn

from loguru import logger

from ..features.article_reading import (
    ArticleNotFoundError as ReadingArticleNotFoundError,
)
from ..features.article_reading import ArticlePageSizeError, ArticleStorageError
from ..shared.exceptions import (
    ArticleNotFoundError,
    InvalidURLError,
    ScraperBlockedError,
    ScraperError,
    ScraperRateLimitedError,
    ScraperTimeoutError,
    SummarizerNotAvailableError,
    SummarizerTokenLimitError,
    UseCaseError,
)
from .errors import MCPToolError
from .input_validator import MCPValidationError


def raise_mcp_error(
    exc: Exception,
    *,
    fallback: tuple[str, str] | None = None,
) -> NoReturn:
    """Raise MCPToolError so the MCP client sees isError instead of a fake success."""
    if isinstance(exc, MCPToolError):
        raise exc
    if isinstance(exc, MCPValidationError):
        raise MCPToolError("invalid_input", str(exc) or "输入参数无效") from exc
    if isinstance(exc, InvalidURLError):
        raise MCPToolError("invalid_input", exc.user_message or "输入参数无效") from exc
    if isinstance(exc, ValueError):
        raise MCPToolError("invalid_input", str(exc) or "输入参数无效") from exc
    if isinstance(exc, ScraperBlockedError):
        raise MCPToolError("scraper_blocked", "抓取被反爬限制") from exc
    if isinstance(exc, ScraperTimeoutError):
        raise MCPToolError("scraper_timeout", "抓取超时") from exc
    if isinstance(exc, ScraperRateLimitedError):
        raise MCPToolError("scraper_rate_limited", "抓取请求被限流") from exc
    if isinstance(exc, ScraperError):
        raise MCPToolError("article_fetch_failed", "抓取文章失败") from exc
    if isinstance(exc, (ReadingArticleNotFoundError, ArticleNotFoundError)):
        raise MCPToolError("article_not_found", "文章不存在") from exc
    if isinstance(exc, ArticleStorageError):
        raise MCPToolError("article_storage_failed", "文章持久化失败") from exc
    if isinstance(exc, ArticlePageSizeError):
        raise MCPToolError("page_too_large", str(exc)) from exc
    if isinstance(exc, SummarizerNotAvailableError):
        raise MCPToolError("summarizer_unavailable", "DeepSeek 摘要器不可用") from exc
    if isinstance(exc, SummarizerTokenLimitError):
        raise MCPToolError("article_too_long", "文章过长，请使用分页 Markdown 阅读") from exc
    if isinstance(exc, UseCaseError):
        code, message = fallback or ("article_summary_failed", "文章摘要失败")
        raise MCPToolError(code, message) from exc
    if fallback is not None:
        logger.exception("MCP 工具未分类异常")
        raise MCPToolError(fallback[0], fallback[1]) from exc
    logger.exception("MCP 工具未处理异常")
    raise MCPToolError("tool_failed", "工具执行失败") from exc
