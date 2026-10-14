"""Compact, provenance-aware Markdown input for LLM summarization."""

from __future__ import annotations

from dataclasses import replace

from ...domain.entities import Article
from .dto import ReadingBlock
from .projection import ArticleReadingProjector, escape_markdown_text
from .renderer import MarkdownReadingRenderer


class SummarySourceRenderer:
    """Render safe article structure while keeping external URLs out of prompts."""

    def __init__(self) -> None:
        self._projector = ArticleReadingProjector()
        self._markdown = MarkdownReadingRenderer()

    def render(self, article: Article) -> str:
        projection = self._projector.project(article)
        parts = [
            f"# {escape_markdown_text(article.title)}",
            "",
            '<article_source trust="untrusted_web_content">',
            self._markdown.render(tuple(_without_urls(block) for block in projection.blocks)),
            "</article_source>",
        ]
        return "\n".join(parts).strip()


def _without_urls(block: ReadingBlock) -> ReadingBlock:
    if block.type == "image":
        description = (
            "；".join(value for value in (block.alt, block.caption, block.title) if value)
            or "未提供说明"
        )
        return replace(
            block,
            type="paragraph",
            text=f"[图片说明：{description}]",
            markdown=f"[图片说明：{escape_markdown_text(description)}]",
            url=None,
        )
    markdown = block.text if block.type in {"table", "rule"} else escape_markdown_text(block.text)
    return replace(
        block,
        markdown=markdown,
        links=(),
        children=tuple(_without_urls(child) for child in block.children),
    )
