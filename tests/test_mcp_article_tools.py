from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wechat_article_reader.features.article_reading import ArticleNotFoundError
from wechat_article_reader.features.article_workflow.dto import (
    ArticleMetadataPayload,
    ArticleSummaryPayload,
    SummaryPayload,
)
from wechat_article_reader.mcp.errors import MCPToolError
from wechat_article_reader.mcp.toolsets.article_tools import register_article_tools
from wechat_article_reader.shared.exceptions import (
    SummarizerNotAvailableError,
    SummarizerTokenLimitError,
)

ARTICLE_ID = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"


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
        config, "get_container", lambda: SimpleNamespace(article_workflow_service=service)
    )
    registry = _Registry()
    register_article_tools(registry)
    return registry


def _summary_payload() -> ArticleSummaryPayload:
    return ArticleSummaryPayload(
        article=ArticleMetadataPayload(
            url="https://mp.weixin.qq.com/s/test",
            title="标题",
            author="作者",
            account_name=None,
            publish_time="",
            word_count=12,
        ),
        summary=SummaryPayload("概述", ("要点",), ("AI",), "一句话"),
    )


def test_summarize_article_uses_cached_id(monkeypatch) -> None:
    service = Mock()
    service.summarize_cached.return_value = _summary_payload()
    registry = _register(monkeypatch, service)

    result = asyncio.run(registry.tools["summarize_article"](ARTICLE_ID))

    assert result["success"] is True
    assert result["source_trust"] == "untrusted_web_content"
    assert result["article_id"] == ARTICLE_ID
    service.summarize_cached.assert_called_once()
    service.summarize.assert_not_called()


def test_summarize_article_maps_not_found(monkeypatch) -> None:
    service = Mock()
    service.summarize_cached.side_effect = ArticleNotFoundError("missing")
    registry = _register(monkeypatch, service)

    with pytest.raises(MCPToolError) as err:
        asyncio.run(registry.tools["summarize_article"](ARTICLE_ID))

    assert err.value.code == "article_not_found"


def test_summarize_article_maps_too_long(monkeypatch) -> None:
    service = Mock()
    service.summarize_cached.side_effect = SummarizerTokenLimitError("too long")
    registry = _register(monkeypatch, service)

    with pytest.raises(MCPToolError) as err:
        asyncio.run(registry.tools["summarize_article"](ARTICLE_ID))

    assert err.value.code == "article_too_long"


def test_summarize_article_maps_unavailable(monkeypatch) -> None:
    service = Mock()
    service.summarize_cached.side_effect = SummarizerNotAvailableError("no key")
    registry = _register(monkeypatch, service)

    with pytest.raises(MCPToolError) as err:
        asyncio.run(registry.tools["summarize_article"](ARTICLE_ID))

    assert err.value.code == "summarizer_unavailable"


def test_article_tools_do_not_register_batch_summarize(monkeypatch) -> None:
    assert "batch_summarize" not in _register(monkeypatch, Mock()).tools


def test_summarize_article_documents_cached_contract(monkeypatch) -> None:
    documentation = inspect.getdoc(_register(monkeypatch, Mock()).tools["summarize_article"])
    assert documentation is not None
    assert "DeepSeek" in documentation
    assert "ingest_article" in documentation
