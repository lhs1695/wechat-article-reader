"""Minimal cached, paginated Markdown reading tools."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from ..error_mapping import raise_mcp_error
from ..execution import run_cancellable
from ..input_validator import MCPInputValidator, MCPValidationError
from ..response_budget import (
    DEFAULT_METADATA_PAGE_SIZE,
    MAX_METADATA_PAGE_SIZE,
    MAX_TOOL_RESPONSE_BYTES,
    MetadataPosition,
    decode_metadata_cursor,
    fit_metadata_response,
    fit_read_response,
    metadata_page,
    serialized_tool_result_size,
    truncate_utf8,
)
from ..security import secure_tool

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register_reading_tools(mcp_instance: FastMCP) -> None:
    from ...features.article_reading.service import DEFAULT_READ_MAX_CHARS, ArticlePageSizeError
    from ...infrastructure.config import get_container

    def service():
        return get_container().article_reading_service

    def metadata_result(
        raw: dict[str, Any],
        *,
        sections_cursor: str | None,
        images_cursor: str | None,
        toc_level: int,
        sections_limit: int,
        images_limit: int,
    ) -> dict[str, Any]:
        sections = raw.pop("sections")
        images = raw.pop("images")
        article_id = str(raw["article_id"])
        revision = str(raw["revision"])
        title, title_truncated = truncate_utf8(str(raw["title"]), 2048)
        raw["title"] = title
        raw["title_truncated"] = title_truncated
        if isinstance(raw.get("url"), str):
            raw["url"], url_truncated = truncate_utf8(raw["url"], 4096)
            raw["url_truncated"] = url_truncated
        for name in ("author", "account_name"):
            if isinstance(raw.get(name), str):
                raw[name], truncated = truncate_utf8(raw[name], 1024)
                raw[f"{name}_truncated"] = truncated

        section_items: list[dict[str, Any]] = []
        for item in sections:
            section = asdict(item) if hasattr(item, "__dataclass_fields__") else dict(item)
            section["title"], section["truncated"] = truncate_utf8(
                str(section.get("title", "")), 2048
            )
            section_items.append(section)
        image_items: list[dict[str, Any]] = []
        for item in images:
            image = dict(item)
            truncated = False
            for name, limit in (("url", 4096), ("alt", 2048), ("caption", 2048)):
                image[name], value_truncated = truncate_utf8(str(image.get(name, "")), limit)
                truncated |= value_truncated
            image["truncated"] = truncated
            image_items.append(image)

        section_position = decode_metadata_cursor(
            sections_cursor,
            article_id=article_id,
            article_revision=revision,
            kind="sections",
            total_items=len(section_items),
            toc_level=toc_level,
        )
        image_position = decode_metadata_cursor(
            images_cursor,
            article_id=article_id,
            article_revision=revision,
            kind="images",
            total_items=len(image_items),
        )
        sections_page, visible_sections = metadata_page(
            items=section_items,
            position=section_position,
            limit=sections_limit,
            article_id=article_id,
            article_revision=revision,
            kind="sections",
            toc_level=toc_level,
            sections_limit=sections_limit,
            images_limit=images_limit,
        )
        images_page, visible_images = metadata_page(
            items=image_items,
            position=image_position,
            limit=images_limit,
            article_id=article_id,
            article_revision=revision,
            kind="images",
            toc_level=toc_level,
            sections_limit=sections_limit,
            images_limit=images_limit,
        )
        raw["sections_page"] = sections_page
        raw["images_page"] = images_page
        return fit_metadata_response(
            raw,
            page_states=[
                ("sections", "sections_page", visible_sections, section_position),
                ("images", "images_page", visible_images, image_position),
            ],
            article_id=article_id,
            article_revision=revision,
            toc_level=toc_level,
            sections_limit=sections_limit,
            images_limit=images_limit,
        )

    @mcp_instance.tool()
    @secure_tool
    async def ingest_article(
        url: str,
        refresh: bool = False,
        toc_level: int = 2,
        sections_limit: int = DEFAULT_METADATA_PAGE_SIZE,
        images_limit: int = DEFAULT_METADATA_PAGE_SIZE,
    ) -> dict[str, Any]:
        """抓取或命中缓存，返回文章元数据及有界的章节、图片清单首屏。

        这是唯一会抓取微信公众号并写 SQLite 的工具。refresh=true 时强制重新抓取。
        要整篇 Markdown 文件时，随后调用 export_article 写盘；不要拼接 read_article 分页。
        默认 toc_level=2（H2）。若文章没有这么浅的标题（例如正文全是 H3），目录自动对齐到最浅一层。
        需要更细的纲时再提高 toc_level。篇幅不超过 read_article 的 max_chars 时一次读完。
        章节和图片清单默认各返回 20 项，可用各自 next 参数从 get_cached_article 续取。
        更长的文章按 sections_page.items 中的标题或 start_cursor 跳读（父章节包含子章节）；
        节内仍超长时跟随 next_read。完整图文理解需另用 read_article_image 核对配图。
        content_notes 以 code/message/scope 报告已观察到的限制，不代表正文完整度判断。
        只需文章概览时，可把返回的 article_id 交给 summarize_article 生成文字摘要；
        该工具不抓取文章，也不代替 read_article 的原文分页阅读。
        """
        try:
            if not isinstance(refresh, bool):
                raise MCPValidationError("refresh must be a boolean")
            sections_limit = MCPInputValidator.validate_int_range(
                sections_limit,
                field_name="sections_limit",
                lower=1,
                upper=MAX_METADATA_PAGE_SIZE,
            )
            images_limit = MCPInputValidator.validate_int_range(
                images_limit,
                field_name="images_limit",
                lower=1,
                upper=MAX_METADATA_PAGE_SIZE,
            )
            toc_level = MCPInputValidator.validate_toc_level(toc_level)
            result = await run_cancellable(
                service().ingest,
                MCPInputValidator.validate_url(url),
                refresh=refresh,
                toc_level=MCPInputValidator.validate_toc_level(toc_level),
            )
            return metadata_result(
                {"success": True, "source_trust": "untrusted_web_content", **result.to_dict()},
                sections_cursor=None,
                images_cursor=None,
                toc_level=toc_level,
                sections_limit=sections_limit,
                images_limit=images_limit,
            )
        except Exception as exc:
            raise_mcp_error(exc, fallback=("article_fetch_failed", "抓取文章失败"))

    @mcp_instance.tool()
    @secure_tool
    async def get_cached_article(
        article_id: str,
        toc_level: int = 2,
        sections_cursor: str | None = None,
        images_cursor: str | None = None,
        sections_limit: int = DEFAULT_METADATA_PAGE_SIZE,
        images_limit: int = DEFAULT_METADATA_PAGE_SIZE,
    ) -> dict[str, Any]:
        """读取已缓存文章元数据，并独立续取章节或图片清单。不联网。

        toc_level 与 ingest_article 相同，默认 2。章节和图片清单分别默认每页 20 项，
        上限各 100 项；响应预算较紧时可能返回更少项目。原样调用对应 page.next。
        过长的标题、URL、alt 或 caption 会提供 UTF-8 安全预览并标记 truncated。
        content_notes 只报告有证据的抓取范围限制，不推测文章是否完整。
        """
        try:
            toc_level = MCPInputValidator.validate_toc_level(toc_level)
            sections_limit = MCPInputValidator.validate_int_range(
                sections_limit,
                field_name="sections_limit",
                lower=1,
                upper=MAX_METADATA_PAGE_SIZE,
            )
            images_limit = MCPInputValidator.validate_int_range(
                images_limit,
                field_name="images_limit",
                lower=1,
                upper=MAX_METADATA_PAGE_SIZE,
            )
            result = await asyncio.to_thread(
                service().get_metadata,
                MCPInputValidator.validate_article_id(article_id),
                toc_level=MCPInputValidator.validate_toc_level(toc_level),
            )
            return metadata_result(
                {"success": True, "source_trust": "untrusted_web_content", **result.to_dict()},
                sections_cursor=sections_cursor,
                images_cursor=images_cursor,
                toc_level=toc_level,
                sections_limit=sections_limit,
                images_limit=images_limit,
            )
        except Exception as exc:
            raise_mcp_error(
                exc,
                fallback=("article_read_failed", "读取文章失败"),
                article_id=article_id,
            )

    @mcp_instance.tool()
    @secure_tool
    async def read_article(
        article_id: str,
        cursor: int = 0,
        max_chars: int = DEFAULT_READ_MAX_CHARS,
        include_images: bool = False,
        section: str | None = None,
        section_start_cursor: int | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """读取一页缓存 Markdown。只读本地，不联网。

        优先传 section（章节标题：精确匹配，或至少两字的唯一前缀）；只返回该节。
        父章节包含子章节。同名标题使用目录的 section_start_cursor 选择。
        与 cursor 同时出现时在该节内续读，cursor 不在节内会明确失败。
        不传 section 时，全文不超过 max_chars 则一次返回。需要续读时把上次响应的
        next_read 原样传入（含 next_cursor 对应的 cursor 和 expected_revision）。
        版本变化返回 article_changed，需重新获取目录。单块含文头超过当前 max_chars
        但不超过 20000 时会整块返回；超过 20000 返回 page_too_large，并提供 export_article 建议。
        include_images=false（默认）排除图片语法、保留说明；图片数量与未读取状态会明确返回。
        为满足完整 MCP 响应的 128 KiB UTF-8 序列化预算，稠密元数据或中文正文可能自动缩短本页，
        以返回的 next_read 为准。章节 / 图片清单各自分页；本页未列出的图片可按 images.next 续取。
        content_notes 报告抓取时观察到的限制，以及本页图片没有被读取的事实；不推断全文完整度。
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
            options: dict[str, Any] = {}
            if section_start_cursor is not None:
                options["section_start_cursor"] = MCPInputValidator.validate_int_range(
                    section_start_cursor,
                    field_name="section_start_cursor",
                    lower=0,
                    upper=2_147_483_647,
                )
            if expected_revision is not None:
                options["expected_revision"] = MCPInputValidator.validate_revision(
                    expected_revision
                )
            normalized_article_id = MCPInputValidator.validate_article_id(article_id)
            normalized_section = MCPInputValidator.validate_optional_section(section)
            attempted: set[int] = set()
            while max_chars not in attempted:
                attempted.add(max_chars)
                result = await run_cancellable(
                    service().read,
                    normalized_article_id,
                    cursor=cursor,
                    max_chars=max_chars,
                    include_images=include_images,
                    section=normalized_section,
                    **options,
                )
                payload = {
                    "success": True,
                    "source_trust": "untrusted_web_content",
                    **result.to_dict(),
                }
                all_images = payload.pop("images")
                images_total = len(all_images)
                visible_images = all_images[:DEFAULT_METADATA_PAGE_SIZE]
                for image in visible_images:
                    truncated = False
                    for field, byte_limit in (("url", 4096), ("alt", 2048), ("caption", 2048)):
                        image[field], was_truncated = truncate_utf8(
                            str(image.get(field, "")), byte_limit
                        )
                        truncated |= was_truncated
                    image["truncated"] = truncated
                image_start = (
                    int(str(all_images[0]["image_id"]).rsplit("-", 1)[1]) - 1 if all_images else 0
                )
                image_end = image_start + images_total
                image_position = MetadataPosition(image_start, image_start, image_end)
                payload["images"], visible_images = metadata_page(
                    items=all_images,
                    items_offset=image_start,
                    position=image_position,
                    limit=DEFAULT_METADATA_PAGE_SIZE,
                    article_id=normalized_article_id,
                    article_revision=result.revision,
                    kind="images",
                    toc_level=2,
                    sections_limit=DEFAULT_METADATA_PAGE_SIZE,
                    images_limit=DEFAULT_METADATA_PAGE_SIZE,
                )
                sections = payload.get("sections_in_page", [])
                bounded_sections = []
                for section_item in sections[:DEFAULT_METADATA_PAGE_SIZE]:
                    section_data = dict(section_item)
                    section_data["title"], section_data["truncated"] = truncate_utf8(
                        str(section_data.get("title", "")), 2048
                    )
                    bounded_sections.append(section_data)
                payload["sections_in_page"] = bounded_sections
                if len(sections) > len(bounded_sections):
                    payload["omitted_sections"] = len(sections) - len(bounded_sections)
                payload["sections_in_page"] = bounded_sections
                payload = fit_read_response(
                    payload,
                    article_id=normalized_article_id,
                    article_revision=result.revision,
                    image_position=image_position,
                    toc_level=2,
                    sections_limit=DEFAULT_METADATA_PAGE_SIZE,
                    images_limit=DEFAULT_METADATA_PAGE_SIZE,
                )
                if serialized_tool_result_size(payload) <= MAX_TOOL_RESPONSE_BYTES:
                    return payload
                if max_chars <= 1_000:
                    break
                max_chars = max(1_000, max_chars - max(1_000, max_chars // 8))
            raise ArticlePageSizeError(
                "当前完整 Markdown 块与 MCP 外层封装超过 128 KiB 响应预算；"
                "请使用 export_article 获取完整文件"
            )
        except Exception as exc:
            raise_mcp_error(
                exc,
                fallback=("article_read_failed", "读取文章失败"),
                article_id=article_id,
                cursor=cursor,
            )

    @mcp_instance.tool()
    @secure_tool
    async def export_article(
        article_id: str,
        path: str,
        include_images: bool = False,
        expected_revision: str | None = None,
        download_images: bool = False,
    ) -> dict[str, Any]:
        """将缓存文章导出 Markdown。默认不联网；download_images=true 时下载微信 CDN 图片。

        path 必须是绝对 .md 文件路径或已存在目录。include_images 仅控制显示，
        download_images 独立控制下载和同名 .assets 图片清单；两个开关默认 false。
        下载失败在 Markdown 和清单中标记；下载不会把图片送入视觉模型。
        成功返回 export_path、可选 image_manifest_path 与已知 content_notes，不回传正文。
        """
        try:
            if not isinstance(include_images, bool):
                raise MCPValidationError("include_images must be a boolean")
            if type(download_images) is not bool:
                raise MCPValidationError("download_images must be a boolean")
            result = await run_cancellable(
                service().export_markdown,
                MCPInputValidator.validate_article_id(article_id),
                path=MCPInputValidator.validate_export_path(path),
                include_images=include_images,
                **({"download_images": True} if download_images else {}),
                **(
                    {"expected_revision": MCPInputValidator.validate_revision(expected_revision)}
                    if expected_revision is not None
                    else {}
                ),
            )
            return {"success": True, "source_trust": "untrusted_web_content", **result.to_dict()}
        except Exception as exc:
            raise_mcp_error(
                exc, fallback=("article_export_failed", "导出文章失败"), article_id=article_id
            )

    @mcp_instance.tool()
    @secure_tool
    async def read_article_image(
        article_id: str,
        image_id: str,
        expected_revision: str,
        download_image: bool = False,
        send_to_model: bool = False,
    ) -> list[Any]:
        """按阅读页 images 编号读取文章图片，不接受任意 URL。

        默认仅返回元数据。download_image=true 允许下载到本地缓存；
        send_to_model=true 才返回 MCP 图像内容，需客户端及模型支持视觉。
        两个开关独立：缓存图片可不联网直接提供；未缓存时须显式允许下载。
        动画仅提供首帧 PNG 预览，并明确标记其他帧未覆盖。
        图文阅读需逐张调用并核对 images 清单；工具无法断言模型已理解图片。
        """
        import base64
        import json

        from mcp.types import ImageContent, TextContent

        try:
            metadata, asset = await run_cancellable(
                service().read_image,
                MCPInputValidator.validate_article_id(article_id),
                image_id,
                expected_revision=MCPInputValidator.validate_revision(expected_revision),
                download_image=download_image,
                send_to_model=send_to_model,
            )
            content: list[Any] = [
                TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False))
            ]
            if asset is not None:
                content.append(
                    ImageContent(
                        type="image",
                        data=base64.b64encode(asset.data).decode("ascii"),
                        mimeType=asset.mime_type,
                    )
                )
            return content
        except Exception as exc:
            raise_mcp_error(
                exc, fallback=("article_image_failed", "图片读取失败"), article_id=article_id
            )
