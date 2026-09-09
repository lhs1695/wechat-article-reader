"""Minimal cached, paginated Markdown reading tools."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ..error_mapping import raise_mcp_error
from ..input_validator import MCPInputValidator, MCPValidationError
from ..security import secure_tool

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register_reading_tools(mcp_instance: FastMCP) -> None:
    from ...features.article_reading.service import DEFAULT_READ_MAX_CHARS
    from ...infrastructure.config import get_container

    def service():
        return get_container().article_reading_service

    @mcp_instance.tool()
    @secure_tool
    async def ingest_article(url: str, refresh: bool = False, toc_level: int = 2) -> dict[str, Any]:
        """抓取或命中缓存，返回 article_id、标题、字数、块数和章节目录。

        这是唯一会抓取微信公众号并写 SQLite 的工具。refresh=true 时强制重新抓取。
        默认 toc_level=2（H2）。若文章没有这么浅的标题（例如正文全是 H3），目录自动对齐到最浅一层。
        需要更细的纲时再提高 toc_level。篇幅不超过 read_article 的 max_chars 时一次读完。
        更长的文章按 sections 标题或 start_cursor 跳读（传 section 只返回该节）；
        只有节内仍超长才跟随 next_cursor。通读长文优先 summarize_article。
        """
        try:
            if not isinstance(refresh, bool):
                raise MCPValidationError("refresh must be a boolean")
            result = await asyncio.to_thread(
                service().ingest,
                MCPInputValidator.validate_url(url),
                refresh=refresh,
                toc_level=MCPInputValidator.validate_toc_level(toc_level),
            )
            return {"success": True, "source_trust": "untrusted_web_content", **result.to_dict()}
        except Exception as exc:
            raise_mcp_error(exc, fallback=("article_fetch_failed", "抓取文章失败"))

    @mcp_instance.tool()
    @secure_tool
    async def get_cached_article(article_id: str, toc_level: int = 2) -> dict[str, Any]:
        """读取已缓存文章元数据。ingest 已返回章节时可不调用。不联网。

        toc_level 与 ingest_article 相同，默认 2。
        """
        try:
            result = await asyncio.to_thread(
                service().get_metadata,
                MCPInputValidator.validate_article_id(article_id),
                toc_level=MCPInputValidator.validate_toc_level(toc_level),
            )
            return {"success": True, "source_trust": "untrusted_web_content", **result.to_dict()}
        except Exception as exc:
            raise_mcp_error(exc, fallback=("article_read_failed", "读取文章失败"))

    @mcp_instance.tool()
    @secure_tool
    async def read_article(
        article_id: str,
        cursor: int = 0,
        max_chars: int = DEFAULT_READ_MAX_CHARS,
        include_images: bool = False,
        section: str | None = None,
    ) -> dict[str, Any]:
        """读取一页缓存 Markdown。只读本地，不联网。

        优先传 section（章节标题：精确匹配，或至少两字的唯一前缀）；只返回该节。
        与 cursor 同时出现时在该节内续读，cursor 不在节内则从节首开始。
        不传 section 时，全文不超过 max_chars 则一次返回。需要续读时把上次响应的
        next_cursor 原样传入。单个块超过当前 max_chars 但不超过
        20000 时会整块返回；超过 20000 才返回 page_too_large。
        include_images=false（默认）排除图片语法、保留说明。
        """
        try:
            cursor = MCPInputValidator.validate_int_range(
                cursor, field_name="cursor", lower=0, upper=2_147_483_647
            )
            max_chars = MCPInputValidator.validate_int_range(
                max_chars, field_name="max_chars", lower=1_000, upper=20_000
            )
            if not isinstance(include_images, bool):
                raise MCPValidationError("include_images must be a boolean")
            result = await asyncio.to_thread(
                service().read,
                MCPInputValidator.validate_article_id(article_id),
                cursor=cursor,
                max_chars=max_chars,
                include_images=include_images,
                section=MCPInputValidator.validate_optional_section(section),
            )
            return {"success": True, "source_trust": "untrusted_web_content", **result.to_dict()}
        except Exception as exc:
            raise_mcp_error(exc, fallback=("article_read_failed", "读取文章失败"))
