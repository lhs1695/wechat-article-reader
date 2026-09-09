"""Concrete construction for the supported local reading workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ...infrastructure.adapters.exporters import HtmlExporter, MarkdownExporter
from ...infrastructure.adapters.scrapers import WechatHttpxScraper
from ...infrastructure.adapters.summarizers import (
    DeepSeekSummarizer,
)

if TYPE_CHECKING:
    from ...application.ports.outbound import ExporterPort, ScraperPort, StoragePort, SummarizerPort
    from .settings import AppSettings


def build_scrapers(settings: AppSettings) -> list[ScraperPort]:
    return [
        WechatHttpxScraper(
            timeout=settings.scraper.timeout,
            max_retries=settings.scraper.max_retries,
            user_agent_rotation=settings.scraper.user_agent_rotation,
            max_response_bytes=settings.scraper.max_response_bytes,
            max_content_chars=settings.scraper.max_content_chars,
            min_request_interval_seconds=settings.scraper.min_request_interval_seconds,
            max_retry_delay_seconds=settings.scraper.max_retry_delay_seconds,
        )
    ]


def build_summarizers(
    settings: AppSettings, extra_api_keys: dict[str, str] | None = None
) -> dict[str, SummarizerPort]:
    api_key = (extra_api_keys or {}).get("deepseek") or settings.deepseek.api_key.get_secret_value()
    if not api_key:
        return {}
    deepseek = DeepSeekSummarizer(
        api_key=api_key,
        model=settings.deepseek.model,
        timeout=settings.deepseek.timeout,
        max_output_tokens=settings.deepseek.max_output_tokens,
        reasoning_token_budget=settings.deepseek.reasoning_token_budget,
        max_retries=settings.deepseek.max_retries,
    )
    return {"deepseek": deepseek}


def build_storage() -> StoragePort:
    from ..persistence import Database, SqlAlchemyStorage, upgrade_database

    upgrade_database()
    return cast("StoragePort", SqlAlchemyStorage(Database()))


def build_exporters(settings: AppSettings) -> dict[str, ExporterPort]:
    return {
        "html": HtmlExporter(output_dir=settings.export.default_output_dir),
        "markdown": MarkdownExporter(output_dir=settings.export.default_output_dir),
    }
