"""Markdown renderer shared by MCP, CLI, and file exporters.

默认不含图片语法（与 read / Markdown 导出一致）；需要图时显式 include_images=True。
"""

from __future__ import annotations

from collections.abc import Iterable

from .dto import ArticleReadingProjection, ReadingBlock


class MarkdownReadingRenderer:
    def render(
        self,
        value: ArticleReadingProjection | Iterable[ReadingBlock],
        *,
        include_images: bool = False,
    ) -> str:
        blocks = value.blocks if isinstance(value, ArticleReadingProjection) else tuple(value)
        rendered = [self.render_block(block, include_images=include_images) for block in blocks]
        return "\n\n".join(part for part in rendered if part).strip()

    @staticmethod
    def render_block(block: ReadingBlock, *, include_images: bool = False) -> str:
        if block.type == "heading":
            return f"{'#' * max(1, min(block.level or 1, 6))} {block.markdown or block.text}"
        if block.type == "list_item":
            return f"- {block.markdown or block.text}"
        if block.type == "quote":
            return "\n".join(f"> {line}" for line in (block.markdown or block.text).splitlines())
        if block.type == "code":
            fence = "````" if "```" in block.text else "```"
            return f"{fence}\n{block.text}\n{fence}"
        if block.type == "table":
            return block.markdown or block.text
        if block.type == "image" and block.url:
            if not include_images:
                description = block.caption or block.alt
                return f"*{description}*" if description and description != "图片" else ""
            label = MarkdownReadingRenderer._escape_label(block.alt or block.caption or "图片")
            safe_url = block.url.replace("<", "%3C").replace(">", "%3E")
            title = f' "{block.title.replace(chr(34), chr(39))}"' if block.title else ""
            image = f"![{label}](<{safe_url}>{title})"
            if block.caption and block.caption != label:
                image += f"\n\n*{block.caption}*"
            return image
        return block.markdown or block.text

    @staticmethod
    def _escape_label(value: str) -> str:
        return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
