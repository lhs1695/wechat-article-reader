from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wechat_article_reader.features.article_reading import (
    ArticleReadingMetadata,
    ArticleReadPage,
    ReadingSection,
)
from wechat_article_reader.mcp.errors import MCPToolError
from wechat_article_reader.mcp.response_budget import (
    MAX_TOOL_RESPONSE_BYTES,
    encode_metadata_cursor,
    serialized_tool_result_size,
)
from wechat_article_reader.mcp.toolsets.reading_tools import register_reading_tools

ARTICLE_ID = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"


def _registry(monkeypatch, metadata: ArticleReadingMetadata):
    from wechat_article_reader.infrastructure import config

    service = Mock()
    service.get_metadata.return_value = metadata
    monkeypatch.setattr(
        config, "get_container", lambda: SimpleNamespace(article_reading_service=service)
    )
    registry = {}

    class FakeMCP:
        def tool(self):
            def register(function):
                registry[function.__name__] = inspect.unwrap(function)
                return function

            return register

    register_reading_tools(FakeMCP())
    return service, registry


def _metadata(*, count: int = 1_000, long_fields: bool = False) -> ArticleReadingMetadata:
    repeat = 4_000 if long_fields else 80
    return ArticleReadingMetadata(
        article_id=ARTICLE_ID,
        url="https://mp.weixin.qq.com/s/" + ("u" * repeat),
        title="文章标题" + ("中" * repeat),
        author="作者" + ("文" * repeat),
        account_name="公众号" + ("号" * repeat),
        publish_time="2026-10-15",
        word_count=count,
        image_count=count,
        fetched_at=None,
        sections=tuple(
            ReadingSection(
                title=f"章节 {index:04d} " + ("节" * (repeat // 4)),
                level=2,
                start_cursor=index,
                end_cursor=index + 1,
            )
            for index in range(count)
        ),
        block_count=count,
        revision="article-revision-1",
        first_cached_at="2026-10-15T00:00:00+00:00",
        images=tuple(
            {
                "image_id": f"image-{index + 1:03d}",
                "url": "https://mmbiz.qpic.cn/" + ("x" * repeat * 2),
                "alt": "图片说明" + ("图" * (repeat // 2)),
                "caption": "配图" + ("注" * (repeat // 2)),
                "block_cursor": index,
            }
            for index in range(count)
        ),
    )


def test_metadata_pagination_is_complete_bounded_and_marks_truncation(monkeypatch) -> None:
    service, registry = _registry(monkeypatch, _metadata(count=1_000))
    get_metadata = registry["get_cached_article"]

    async def collect(kind: str) -> list[dict[str, object]]:
        response = await get_metadata(ARTICLE_ID, sections_limit=100, images_limit=100)
        collected = list(response[f"{kind}_page"]["items"])
        assert serialized_tool_result_size(response) <= MAX_TOOL_RESPONSE_BYTES
        page = response[f"{kind}_page"]
        while page["next"] is not None:
            response = await get_metadata(**page["next"]["arguments"])
            assert serialized_tool_result_size(response) <= MAX_TOOL_RESPONSE_BYTES
            page = response[f"{kind}_page"]
            collected.extend(page["items"])
        return collected

    sections = asyncio.run(collect("sections"))
    images = asyncio.run(collect("images"))

    assert len(sections) == len(images) == 1_000
    assert [item["start_cursor"] for item in sections] == list(range(1_000))
    assert [item["image_id"] for item in images] == [
        f"image-{index + 1:03d}" for index in range(1_000)
    ]
    assert service.get_metadata.call_count > 10


def test_fastmcp_outer_result_respects_budget_and_exposes_cursors(monkeypatch) -> None:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import CallToolResult

    from wechat_article_reader.infrastructure import config

    service = Mock()
    service.get_metadata.return_value = _metadata(count=250)
    monkeypatch.setattr(
        config, "get_container", lambda: SimpleNamespace(article_reading_service=service)
    )
    mcp = FastMCP("B1 response budget test")
    register_reading_tools(mcp)

    async def call_and_check():
        tools = await mcp.list_tools()
        get_cached = next(tool for tool in tools if tool.name == "get_cached_article")
        assert "sections_cursor" in get_cached.inputSchema["properties"]
        converted = await mcp.call_tool(
            "get_cached_article",
            {"article_id": ARTICLE_ID, "sections_limit": 100, "images_limit": 100},
        )
        if isinstance(converted, tuple):
            content, structured = converted
        else:
            content, structured = converted, None
        result = CallToolResult(content=list(content), structuredContent=structured)
        assert len(result.model_dump_json().encode("utf-8")) <= MAX_TOOL_RESPONSE_BYTES
        return structured

    structured = asyncio.run(call_and_check())
    assert structured["sections_page"]["next"]["tool"] == "get_cached_article"
    assert structured["images_page"]["next"]["tool"] == "get_cached_article"


def test_long_metadata_fields_are_utf8_safe_and_flagged(monkeypatch) -> None:
    _, registry = _registry(monkeypatch, _metadata(count=1, long_fields=True))

    response = asyncio.run(registry["get_cached_article"](ARTICLE_ID))
    section = response["sections_page"]["items"][0]
    image = response["images_page"]["items"][0]

    assert section["truncated"] is True
    assert len(section["title"].encode("utf-8")) <= 2_048
    assert image["truncated"] is True
    assert len(image["url"].encode("utf-8")) <= 4_096
    assert len(image["alt"].encode("utf-8")) <= 2_048
    assert len(image["caption"].encode("utf-8")) <= 2_048
    assert serialized_tool_result_size(response) <= MAX_TOOL_RESPONSE_BYTES


def test_stale_metadata_cursor_requires_refetch(monkeypatch) -> None:
    _, registry = _registry(monkeypatch, _metadata(count=2))
    cursor = encode_metadata_cursor(
        article_id=ARTICLE_ID,
        article_revision="old-revision",
        kind="sections",
        offset=1,
        scope_start=0,
        scope_end=2,
    )

    with pytest.raises(MCPToolError) as error:
        asyncio.run(registry["get_cached_article"](ARTICLE_ID, sections_cursor=cursor))

    assert error.value.code == "article_changed"
    assert error.value.recovery["tool"] == "get_cached_article"
    assert error.value.recovery["arguments"] == {"article_id": ARTICLE_ID}


def test_section_cursor_rejects_changed_toc_depth(monkeypatch) -> None:
    _, registry = _registry(monkeypatch, _metadata(count=2))
    cursor = encode_metadata_cursor(
        article_id=ARTICLE_ID,
        article_revision="article-revision-1",
        kind="sections",
        offset=1,
        scope_start=0,
        scope_end=2,
        toc_level=2,
    )

    with pytest.raises(MCPToolError) as error:
        asyncio.run(registry["get_cached_article"](ARTICLE_ID, toc_level=3, sections_cursor=cursor))

    assert error.value.code == "invalid_input"
    assert "目录深度已变化" in error.value.message


def test_read_article_adapts_to_complete_utf8_envelope_budget(monkeypatch) -> None:
    service, registry = _registry(monkeypatch, _metadata(count=0))

    def read(_article_id: str, *, cursor: int, max_chars: int, **_kwargs):
        content = "😀" * max_chars
        next_cursor = cursor + max_chars
        return ArticleReadPage(
            article_id=ARTICLE_ID,
            cursor=cursor,
            next_cursor=next_cursor,
            has_more=True,
            content_markdown=content,
            chars=len(content),
            revision="article-revision-1",
            next_read={
                "article_id": ARTICLE_ID,
                "cursor": next_cursor,
                "expected_revision": "article-revision-1",
                "max_chars": max_chars,
                "include_images": False,
            },
        )

    service.read.side_effect = read
    response = asyncio.run(
        registry["read_article"](ARTICLE_ID, max_chars=20_000, include_images=False)
    )

    assert response["success"] is True
    assert response["chars"] < 20_000
    assert response["next_read"]["max_chars"] == response["chars"]
    assert serialized_tool_result_size(response) <= MAX_TOOL_RESPONSE_BYTES
    assert service.read.call_count > 1


def test_pure_image_read_page_uses_scoped_metadata_continuation(monkeypatch) -> None:
    service, registry = _registry(monkeypatch, _metadata(count=1_000))
    service.read.return_value = ArticleReadPage(
        article_id=ARTICLE_ID,
        cursor=0,
        next_cursor=None,
        has_more=False,
        content_markdown="",
        revision="article-revision-1",
        image_count=1_000,
        images=tuple(
            {
                "image_id": f"image-{index + 1:03d}",
                "url": f"https://mmbiz.qpic.cn/{index}",
                "alt": "图片",
                "caption": "图注",
                "block_cursor": index,
            }
            for index in range(1_000)
        ),
    )

    async def read_then_continue():
        first = await registry["read_article"](ARTICLE_ID)
        assert len(first["images"]["items"]) == 20
        assert first["images"]["total_items"] == 1_000
        assert serialized_tool_result_size(first) <= MAX_TOOL_RESPONSE_BYTES
        arguments = first["images"]["next"]["arguments"]
        next_page = await registry["get_cached_article"](**arguments)
        return first, next_page

    first, next_page = asyncio.run(read_then_continue())

    assert first["images"]["items"][0]["image_id"] == "image-001"
    assert next_page["images_page"]["range_start"] == 20
    assert next_page["images_page"]["items"][0]["image_id"] == "image-021"
    assert serialized_tool_result_size(next_page) <= MAX_TOOL_RESPONSE_BYTES


def test_read_page_shrinks_long_inline_image_metadata_before_text(monkeypatch) -> None:
    service, registry = _registry(monkeypatch, _metadata(count=100, long_fields=True))

    def read(_article_id: str, *, cursor: int, max_chars: int, **_kwargs):
        content = "😀" * max_chars
        return ArticleReadPage(
            article_id=ARTICLE_ID,
            cursor=cursor,
            next_cursor=cursor + max_chars,
            has_more=True,
            content_markdown=content,
            chars=len(content),
            revision="article-revision-1",
            image_count=100,
            images=tuple(
                {
                    "image_id": f"image-{index + 1:03d}",
                    "url": "https://mmbiz.qpic.cn/" + ("x" * 5_000),
                    "alt": "图片" + ("图" * 1_000),
                    "caption": "图注" + ("注" * 1_000),
                    "block_cursor": index,
                }
                for index in range(100)
            ),
        )

    service.read.side_effect = read

    async def read_then_continue():
        first = await registry["read_article"](ARTICLE_ID, max_chars=20_000)
        next_page = await registry["get_cached_article"](**first["images"]["next"]["arguments"])
        return first, next_page

    first, next_page = asyncio.run(read_then_continue())

    assert len(first["images"]["items"]) < 20
    assert first["images"]["next"] is not None
    assert first["chars"] < 20_000
    assert serialized_tool_result_size(first) <= MAX_TOOL_RESPONSE_BYTES
    next_offset = first["images"]["range_end"]
    assert next_page["images_page"]["range_start"] == next_offset
    assert next_page["images_page"]["items"][0]["image_id"] == (f"image-{next_offset + 1:03d}")
    assert serialized_tool_result_size(next_page) <= MAX_TOOL_RESPONSE_BYTES
