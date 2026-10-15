"""DeepSeek summary MCP tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.fastmcp import Context

from ...domain.value_objects import ContentNote
from ...shared.constants import DEFAULT_SUMMARY_MAX_LENGTH
from ..error_mapping import raise_mcp_error
from ..execution import report_operation_phase, run_cancellable
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
        article_id: str,
        max_length: int = DEFAULT_SUMMARY_MAX_LENGTH,
        ctx: Context | None = None,
    ) -> dict[str, object]:
        """对已缓存文章用 DeepSeek 生成摘要；版本和条件相同则复用摘要。不抓公众号。

        单次调用共享 180 秒总预算；宿主提供 progress token 时报告摘要阶段。
        需要先 ingest_article。未缓存 → article_not_found；超长 → article_too_long；
        未配置密钥 → summarizer_unavailable。结果含 content_notes；摘要只覆盖可读取的文字和可用图注，
        不代表图片已被视觉分析或文章已完整。
        """
        try:
            await report_operation_phase(ctx, 1, "检查摘要缓存与生成条件")
            payload = await run_cancellable(
                service().summarize_cached,
                MCPInputValidator.validate_article_id(article_id),
                MCPInputValidator.validate_max_length(max_length),
            )
            await report_operation_phase(ctx, 2, "摘要已生成或从缓存复用")
            return {
                "success": True,
                "source_trust": "untrusted_web_content",
                "article_id": article_id,
                "title": payload.article.title,
                "author": payload.article.author,
                "word_count": payload.article.word_count,
                "revision": payload.article.revision,
                "summary": payload.summary.to_dict(),
                "content_notes": [
                    *payload.article.content_notes,
                    ContentNote(
                        "summary_text_only",
                        "摘要只覆盖可读取的正文文字和可用图注；不代表图片已被视觉分析",
                        "summary",
                    ).to_dict(),
                ],
            }
        except Exception as exc:
            raise_mcp_error(
                exc, fallback=("article_summary_failed", "文章摘要失败"), article_id=article_id
            )
