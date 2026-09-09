"""Delivery-neutral DTOs for deterministic article reading."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ReadingLink:
    """A safe inline link retained from the sanitized article HTML."""

    text: str
    url: str
    title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class ReadingBlock:
    id: str
    index: int
    type: str
    text: str = ""
    level: int | None = None
    url: str | None = None
    alt: str | None = None
    title: str | None = None
    caption: str | None = None
    markdown: str | None = None
    links: tuple[ReadingLink, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = {key: value for key, value in asdict(self).items() if value is not None}
        payload["links"] = [link.to_dict() for link in self.links]
        return payload


@dataclass(frozen=True)
class ReadingSection:
    title: str
    level: int
    start_cursor: int
    end_cursor: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArticleReadingProjection:
    article_id: str
    content_hash: str
    blocks: tuple[ReadingBlock, ...]
    sections: tuple[ReadingSection, ...]


@dataclass(frozen=True)
class IngestResult:
    article_id: str
    title: str
    word_count: int
    image_count: int
    cached: bool
    sections: tuple[ReadingSection, ...] = ()
    block_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["sections"] = [section.to_dict() for section in self.sections]
        return payload


@dataclass(frozen=True)
class ArticleReadingMetadata:
    article_id: str
    url: str
    title: str
    author: str | None
    account_name: str | None
    publish_time: str
    word_count: int
    image_count: int
    fetched_at: str | None
    sections: tuple[ReadingSection, ...]
    block_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["sections"] = [section.to_dict() for section in self.sections]
        return payload


@dataclass(frozen=True)
class ArticleReadPage:
    article_id: str
    cursor: int
    next_cursor: int | None
    has_more: bool
    content_markdown: str
    block_count: int = 0
    word_count: int = 0
    section_title: str | None = None
    chars: int = 0
    section_end_cursor: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "article_id": self.article_id,
            "cursor": self.cursor,
            "next_cursor": self.next_cursor,
            "has_more": self.has_more,
            "content_markdown": self.content_markdown,
            "block_count": self.block_count,
            "word_count": self.word_count,
            "section_title": self.section_title,
            "chars": self.chars if self.chars else len(self.content_markdown),
            "section_end_cursor": self.section_end_cursor,
        }
