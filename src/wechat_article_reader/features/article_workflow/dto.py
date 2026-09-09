"""Delivery-neutral DTOs for the article workflow."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArticleMetadataPayload:
    url: str
    title: str
    author: str | None
    account_name: str | None
    publish_time: str
    word_count: int


@dataclass(frozen=True)
class ArticleFetchPayload(ArticleMetadataPayload):
    overview: str
    content_html: str
    content_truncated: bool


@dataclass(frozen=True)
class ArticleInfoPayload(ArticleMetadataPayload):
    preview: str


@dataclass(frozen=True)
class SummaryPayload:
    overview: str
    key_points: tuple[str, ...]
    tags: tuple[str, ...]
    one_sentence: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "overview": self.overview,
            "key_points": list(self.key_points),
            "tags": list(self.tags),
            "one_sentence": self.one_sentence,
        }


@dataclass(frozen=True)
class ArticleSummaryPayload:
    article: ArticleMetadataPayload
    summary: SummaryPayload


@dataclass(frozen=True)
class ArticleProcessPayload:
    article: ArticleMetadataPayload
    content: str
    content_html: str
    summary: SummaryPayload | None = None
    export_path: str | None = None
    summary_error: str | None = None


@dataclass(frozen=True)
class BatchProcessItemPayload:
    url: str
    success: bool
    result: ArticleProcessPayload | None = None
    error: str | None = None


@dataclass(frozen=True)
class BatchProcessPayload:
    total: int
    processed: int
    results: tuple[BatchProcessItemPayload, ...]


@dataclass(frozen=True)
class BatchSummaryItemPayload:
    url: str
    success: bool
    title: str | None = None
    summary: str | None = None
    key_points: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    error: str | None = None
    article_id: str | None = None


@dataclass(frozen=True)
class BatchSummaryPayload:
    total: int
    processed: int
    results: tuple[BatchSummaryItemPayload, ...]


@dataclass(frozen=True)
class ArticleExportPayload:
    article: ArticleMetadataPayload
    export_path: str
    summarized: bool


@dataclass(frozen=True)
class BatchExportItemPayload:
    url: str
    success: bool
    title: str | None = None
    export_path: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class BatchExportPayload:
    total: int
    processed: int
    results: tuple[BatchExportItemPayload, ...]
