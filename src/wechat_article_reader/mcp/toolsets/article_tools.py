"""DeepSeek summary MCP tool."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ...shared.constants import DEFAULT_SUMMARY_MAX_LENGTH
from ..error_mapping import raise_mcp_error
from ..input_validator import MCPInputValidator
from ..security import secure_tool

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register_article_tools(mcp_instance: FastMCP) -> None:
    from ...infrastructure.config import get_container

    def service():
        return get_container().article_workflow_service

    @mcp_instance.tool()
    @secure_tool
    async def summarize_article(
        article_id: str, max_length: int = DEFAULT_SUMMARY_MAX_LENGTH
    ) -> dict[str, object]:
        """对已缓存文章调用一次 DeepSeek。会访问 DeepSeek，不抓公众号。

        需要先 ingest_article。未缓存 → article_not_found；超长 → article_too_long；
        未配置密钥 → summarizer_unavailable。
        """
        try:
            payload = await asyncio.to_thread(
                service().summarize_cached,
                MCPInputValidator.validate_article_id(article_id),
                MCPInputValidator.validate_max_length(max_length),
            )
            return {
                "success": True,
                "source_trust": "untrusted_web_content",
                "article_id": article_id,
                "title": payload.article.title,
                "author": payload.article.author,
                "word_count": payload.article.word_count,
                "summary": payload.summary.to_dict(),
            }
        except Exception as exc:
            raise_mcp_error(exc, fallback=("article_summary_failed", "文章摘要失败"))
