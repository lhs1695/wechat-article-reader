"""Markdown renderer shared by MCP, CLI, and file exporters.

默认不含图片语法（与 read / Markdown 导出一致）；需要图时显式 include_images=True。
"""

from __future__ import annotations

from collections.abc import Iterable

from .dto import ArticleReadingProjection, ReadingBlock
from .projection import code_fence, escape_markdown_text, markdown_destination, markdown_title


class MarkdownReadingRenderer:
    def render(
        self,
        value: ArticleReadingProjection | Iterable[ReadingBlock],
        *,
        include_images: bool = False,
    ) -> str:
        blocks = value.blocks if isinstance(value, ArticleReadingProjection) else tuple(value)
        parts: list[str] = []
        previous: ReadingBlock | None = None
        for block in blocks:
            part = self.render_block(block, include_images=include_images)
            if not part:
                continue
            if (
                previous
                and previous.type == block.type == "list_item"
                and previous.list_group != block.list_group
            ):
                parts.append("<!-- list boundary -->")
            if block.continues_previous and parts and block.type == "paragraph":
                parts[-1] += part
            else:
                parts.append(part)
            previous = block
        return "\n\n".join(parts).strip()

    @staticmethod
    def render_block(block: ReadingBlock, *, include_images: bool = False) -> str:
        if block.type == "heading":
            return f"{'#' * max(1, min(block.level or 1, 6))} {block.markdown or block.text}"
        if block.type == "list_item":
            body = (
                MarkdownReadingRenderer().render(block.children, include_images=include_images)
                if block.children
                else (block.markdown or escape_markdown_text(block.text))
            )
            marker = block.list_marker or "-"
            indent = " " * (len(marker) + 1)
            lines = body.splitlines() or [""]
            return f"{marker} {lines[0]}" + "".join(
                f"\n{indent}{line}" if line else "\n" for line in lines[1:]
            )
        if block.type == "quote":
            body = (
                MarkdownReadingRenderer().render(block.children, include_images=include_images)
                if block.children
                else (block.markdown or escape_markdown_text(block.text))
            )
            return "\n".join(f"> {line}" for line in body.splitlines())
        if block.type == "code":
            fence = code_fence(block.text)
            ending = "" if block.text.endswith("\n") else "\n"
            return f"{fence}{block.language or ''}\n{block.text}{ending}{fence}"
        if block.type == "table":
            if (
                block.markdown
                and block.markdown.lstrip().startswith("<table")
                and not include_images
            ):
                from bs4 import BeautifulSoup

                table = BeautifulSoup(block.markdown, "html.parser")
                for table_image in table.find_all("img"):
                    table_image.replace_with(str(table_image.get("alt") or ""))
                return str(table)
            return block.markdown or block.text
        if block.type == "image" and block.url:
            if not include_images:
                description = block.caption or block.alt
                return (
                    f"*{escape_markdown_text(description)}*"
                    if description and description != "图片"
                    else ""
                )
            label = MarkdownReadingRenderer._escape_label(block.alt or block.caption or "图片")
            safe_url = markdown_destination(block.url)
            title = markdown_title(block.title)
            image = f"![{label}](<{safe_url}>{title})"
            if block.caption and block.caption != (block.alt or block.caption or "图片"):
                image += f"\n\n*{escape_markdown_text(block.caption)}*"
            return image
        return block.markdown or block.text

    @staticmethod
    def _escape_label(value: str) -> str:
        return escape_markdown_text(" ".join(value.split()))
