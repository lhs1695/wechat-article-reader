from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wechat_article_reader.features.article_reading import (
    ArticlePageSizeError,
    ArticleReadingMetadata,
    ArticleReadPage,
    IngestResult,
)
from wechat_article_reader.mcp.errors import MCPToolError
from wechat_article_reader.mcp.toolsets.reading_tools import register_reading_tools
from wechat_article_reader.shared.exceptions import ScraperBlockedError, ScraperTimeoutError


class _Registry:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self):
        def register(function):
            self.tools[function.__name__] = inspect.unwrap(function)
            return function

        return register


def _register(monkeypatch, service: Mock) -> _Registry:
    from wechat_article_reader.infrastructure import config

    monkeypatch.setattr(
        config, "get_container", lambda: SimpleNamespace(article_reading_service=service)
    )
    registry = _Registry()
    register_reading_tools(registry)
    return registry


def test_registers_reading_tools(monkeypatch) -> None:
    registry = _register(monkeypatch, Mock())
    assert set(registry.tools) == {
        "ingest_article",
        "get_cached_article",
        "read_article",
    }


def test_read_article_defaults_to_markdown_without_blocks(monkeypatch) -> None:
    service = Mock()
    service.read.return_value = ArticleReadPage(
        article_id="6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        cursor=0,
        next_cursor=None,
        has_more=False,
        content_markdown="## 标题\n\n正文",
    )
    registry = _register(monkeypatch, service)

    result = asyncio.run(registry.tools["read_article"]("6ba7b810-9dad-11d1-80b4-00c04fd430c8"))

    assert result["success"] is True
    assert result["source_trust"] == "untrusted_web_content"
    assert result["content_markdown"].startswith("##")
    assert "blocks" not in result
    service.read.assert_called_once_with(
        "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        cursor=0,
        max_chars=20_000,
        include_images=False,
        section=None,
    )


def test_read_article_can_exclude_images(monkeypatch) -> None:
    service = Mock()
    service.read.return_value = ArticleReadPage(
        article_id="6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        cursor=0,
        next_cursor=None,
        has_more=False,
        content_markdown="正文",
    )
    registry = _register(monkeypatch, service)

    result = asyncio.run(
        registry.tools["read_article"]("6ba7b810-9dad-11d1-80b4-00c04fd430c8", include_images=False)
    )

    assert result["success"] is True
    service.read.assert_called_once_with(
        "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        cursor=0,
        max_chars=20_000,
        include_images=False,
        section=None,
    )


def test_ingest_returns_stable_article_id(monkeypatch) -> None:
    service = Mock()
    service.ingest.return_value = IngestResult(
        article_id="6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        title="文章",
        word_count=10,
        image_count=1,
        cached=True,
    )
    registry = _register(monkeypatch, service)

    result = asyncio.run(registry.tools["ingest_article"]("https://mp.weixin.qq.com/s/test"))

    assert result["success"] is True
    assert result["cached"] is True
    assert "sections" in result


def test_get_cached_article_rejects_invalid_uuid(monkeypatch) -> None:
    registry = _register(monkeypatch, Mock())

    with pytest.raises(MCPToolError) as err:
        asyncio.run(registry.tools["get_cached_article"]("not-a-uuid"))

    assert err.value.code == "invalid_input"
    assert "UUID" in err.value.message


def test_read_article_reports_page_too_large(monkeypatch) -> None:
    service = Mock()
    service.read.side_effect = ArticlePageSizeError("下一个完整 Markdown 块有 21000 字")
    registry = _register(monkeypatch, service)

    with pytest.raises(MCPToolError) as err:
        asyncio.run(registry.tools["read_article"]("6ba7b810-9dad-11d1-80b4-00c04fd430c8"))

    assert err.value.code == "page_too_large"
    assert "21000" in err.value.message


def test_read_article_explains_pagination_parameter_range(monkeypatch) -> None:
    registry = _register(monkeypatch, Mock())

    with pytest.raises(MCPToolError) as err:
        asyncio.run(
            registry.tools["read_article"]("6ba7b810-9dad-11d1-80b4-00c04fd430c8", max_chars=800)
        )

    assert err.value.code == "invalid_input"
    assert "1000" in err.value.message


def test_read_article_documents_pagination_contract(monkeypatch) -> None:
    registry = _register(monkeypatch, Mock())

    documentation = inspect.getdoc(registry.tools["read_article"])

    assert documentation is not None
    assert "next_cursor" in documentation
    assert "20000" in documentation
    assert "include_images=false" in documentation
    assert "section" in documentation


def test_ingest_article_documents_section_first_reading(monkeypatch) -> None:
    registry = _register(monkeypatch, Mock())

    documentation = inspect.getdoc(registry.tools["ingest_article"])

    assert documentation is not None
    assert "toc_level" in documentation
    assert "summarize_article" in documentation
    assert "start_cursor" in documentation


def test_ingest_passes_toc_level(monkeypatch) -> None:
    service = Mock()
    service.ingest.return_value = IngestResult(
        article_id="6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        title="文章",
        word_count=10,
        image_count=1,
        cached=True,
        block_count=4,
    )
    registry = _register(monkeypatch, service)

    asyncio.run(registry.tools["ingest_article"]("https://mp.weixin.qq.com/s/test", toc_level=3))

    service.ingest.assert_called_once_with(
        "https://mp.weixin.qq.com/s/test", refresh=False, toc_level=3
    )


def test_read_article_passes_section(monkeypatch) -> None:
    service = Mock()
    service.read.return_value = ArticleReadPage(
        article_id="6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        cursor=10,
        next_cursor=None,
        has_more=False,
        content_markdown="结语",
        section_title="结语",
    )
    registry = _register(monkeypatch, service)

    asyncio.run(
        registry.tools["read_article"](
            "6ba7b810-9dad-11d1-80b4-00c04fd430c8", cursor=3, section="结语"
        )
    )

    service.read.assert_called_once_with(
        "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        cursor=3,
        max_chars=20_000,
        include_images=False,
        section="结语",
    )


def test_ingest_maps_scraper_timeout(monkeypatch) -> None:
    service = Mock()
    service.ingest.side_effect = ScraperTimeoutError("timeout")
    registry = _register(monkeypatch, service)

    with pytest.raises(MCPToolError) as err:
        asyncio.run(registry.tools["ingest_article"]("https://mp.weixin.qq.com/s/test"))

    assert err.value.code == "scraper_timeout"


def test_ingest_maps_scraper_blocked(monkeypatch) -> None:
    service = Mock()
    service.ingest.side_effect = ScraperBlockedError("blocked")
    registry = _register(monkeypatch, service)

    with pytest.raises(MCPToolError) as err:
        asyncio.run(registry.tools["ingest_article"]("https://mp.weixin.qq.com/s/test"))

    assert err.value.code == "scraper_blocked"


def test_ingest_rejects_non_wechat_host(monkeypatch) -> None:
    registry = _register(monkeypatch, Mock())

    with pytest.raises(MCPToolError) as err:
        asyncio.run(registry.tools["ingest_article"]("https://example.com/article"))

    assert err.value.code == "invalid_input"
    assert "WeChat" in err.value.message


def _register_wrapped(monkeypatch, service: Mock) -> _Registry:
    from wechat_article_reader.infrastructure import config

    monkeypatch.setattr(
        config, "get_container", lambda: SimpleNamespace(article_reading_service=service)
    )
    registry = _Registry()

    def tool():
        def register(function):
            registry.tools[function.__name__] = function
            return function

        return register

    registry.tool = tool  # type: ignore[method-assign]
    register_reading_tools(registry)
    return registry


def test_repeated_read_article_is_not_rate_limited_by_default(monkeypatch) -> None:
    service = Mock()
    service.read.return_value = ArticleReadPage(
        article_id="6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        cursor=0,
        next_cursor=None,
        has_more=False,
        content_markdown="正文",
    )
    registry = _register_wrapped(monkeypatch, service)
    article_id = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"

    for _ in range(30):
        result = asyncio.run(registry.tools["read_article"](article_id))
        assert result["success"] is True


def test_metadata_dto_is_json_ready() -> None:
    metadata = ArticleReadingMetadata(
        article_id="id",
        url="https://mp.weixin.qq.com/s/test",
        title="文章",
        author=None,
        account_name=None,
        publish_time="未知",
        word_count=10,
        image_count=0,
        fetched_at=None,
        sections=(),
    )
    assert metadata.to_dict()["sections"] == []
