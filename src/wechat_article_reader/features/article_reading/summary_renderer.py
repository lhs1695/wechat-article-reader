"""Compact, provenance-aware Markdown input for LLM summarization."""

from __future__ import annotations

from ...domain.entities import Article
from .projection import ArticleReadingProjector
from .renderer import MarkdownReadingRenderer


class SummarySourceRenderer:
    """Render safe article structure while keeping external URLs out of prompts."""

    def __init__(self) -> None:
        self._projector = ArticleReadingProjector()
        self._markdown = MarkdownReadingRenderer()

    def render(self, article: Article) -> str:
        projection = self._projector.project(article)
        parts = [f"# {article.title}", "", '<article_source trust="untrusted_web_content">']
        for block in projection.blocks:
            if block.type == "image":
                description = (
                    "；".join(value for value in (block.alt, block.caption, block.title) if value)
                    or "未提供说明"
                )
                parts.append(f"[图片说明：{description}]")
            elif block.type == "heading":
                parts.append(f"{'#' * max(1, block.level or 1)} {block.text}")
            elif block.type == "list_item":
                parts.append(f"- {block.text}")
            elif block.type == "quote":
                parts.append("\n".join(f"> {line}" for line in block.text.splitlines()))
            elif block.type == "code":
                parts.append(self._markdown.render_block(block))
            elif block.type == "table":
                # ``text`` retains table structure and link labels but excludes URLs.
                parts.append(block.text)
            else:
                parts.append(block.text)
            parts.append("")
        parts.append("</article_source>")
        return "\n".join(parts).strip()
