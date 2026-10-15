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
        has_images = any(block.type == "image" for block in projection.blocks)
        parts = [
            f"# {escape_markdown_text(article.title)}",
            "",
            "以下摘要仅依据可读取的正文文字和可用图注；图片没有被视觉分析，不要推断图中信息。"
            if has_images
            else "以下摘要仅依据可读取的正文文字；不要推断未提供的媒体信息。",
            "",
            *(
                [
                    "已知采集限制："
                    + "；".join(note.message for note in projection.notes)
                    + "。这些限制只说明已观察到的情况，不代表对全文完整性的判断。",
                    "",
                ]
                if projection.notes
                else []
            ),
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
