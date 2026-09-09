"""Build a deterministic, model-friendly reading projection from article HTML."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag

from ...domain.entities import Article
from .dto import ArticleReadingProjection, ReadingBlock, ReadingLink, ReadingSection

_ATOMIC_TAG_TYPES = {
    "blockquote": "quote",
    "li": "list_item",
    "pre": "code",
    "table": "table",
}
_CONTAINER_TAGS = {"article", "div", "main", "section"}
_TEXT_BLOCK_TYPES = {"heading", "paragraph", "list_item", "quote", "code", "table"}
_MAX_BLOCK_CHARS = 900


def content_digest(content_html: str, fallback_text: str = "") -> str:
    """Hash the delivery-relevant source, not just its extracted text."""
    source = content_html or fallback_text
    normalized = "\n".join(line.strip() for line in source.splitlines() if line.strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _clean_text(value: str) -> str:
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines())
    return "\n".join(line for line in lines if line)


def _split_text(value: str, limit: int = _MAX_BLOCK_CHARS) -> list[str]:
    value = _clean_text(value)
    if len(value) <= limit:
        return [value] if value else []

    parts: list[str] = []
    remaining = value
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        window = remaining[: limit + 1]
        cut = max(
            window.rfind("\n"),
            window.rfind("。"),
            window.rfind("！"),
            window.rfind("？"),
            window.rfind("；"),
            window.rfind(" "),
        )
        if cut < limit // 2:
            cut = limit
        else:
            cut += 1
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return [part for part in parts if part]


def _safe_image_url(value: object) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else ""
    candidate = str(value or "").strip()
    parsed = urlsplit(candidate)
    return candidate if parsed.scheme == "https" and parsed.netloc else None


def _safe_link_url(value: object) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else ""
    candidate = str(value or "").strip()
    parsed = urlsplit(candidate)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return candidate
    if parsed.scheme == "mailto" and parsed.path:
        return candidate
    return None


def _attribute(tag: Tag, name: str) -> str | None:
    value = tag.get(name)
    if isinstance(value, list):
        value = value[0] if value else ""
    normalized = _clean_text(str(value or ""))
    return normalized or None


@dataclass(frozen=True)
class _InlineSegment:
    text: str
    markdown: str
    link: ReadingLink | None = None


@dataclass(frozen=True)
class _InlineContent:
    segments: tuple[_InlineSegment, ...]

    @property
    def text(self) -> str:
        return _clean_text("".join(segment.text for segment in self.segments))

    @property
    def markdown(self) -> str:
        return _clean_text("".join(segment.markdown for segment in self.segments))

    @property
    def links(self) -> tuple[ReadingLink, ...]:
        return tuple(segment.link for segment in self.segments if segment.link is not None)


def _table_content(table: Tag) -> _InlineContent:
    text_rows: list[list[str]] = []
    markdown_rows: list[list[str]] = []
    links: list[ReadingLink] = []
    for row in table.find_all("tr"):
        text_cells: list[str] = []
        markdown_cells: list[str] = []
        for cell in row.find_all(["th", "td"]):
            content = _inline_content(cell)
            text_cells.append(content.text.replace("|", "\\|"))
            markdown_cells.append(content.markdown.replace("|", "\\|"))
            links.extend(content.links)
        if text_cells:
            text_rows.append(text_cells)
            markdown_rows.append(markdown_cells)
    if not text_rows:
        content = _inline_content(table)
        return content
    width = max(len(row) for row in text_rows)
    text_rows = [row + [""] * (width - len(row)) for row in text_rows]
    markdown_rows = [row + [""] * (width - len(row)) for row in markdown_rows]

    def render(rows: list[list[str]]) -> str:
        return "\n".join(
            [f"| {' | '.join(rows[0])} |", f"| {' | '.join(['---'] * width)} |"]
            + [f"| {' | '.join(row)} |" for row in rows[1:]]
        )

    return _InlineContent(
        (
            _InlineSegment(render(text_rows), render(markdown_rows)),
            *(_InlineSegment("", "", link) for link in links),
        )
    )


def _escape_markdown_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _markdown_link(link: ReadingLink) -> str:
    url = link.url.replace("<", "%3C").replace(">", "%3E")
    title = f' "{link.title.replace(chr(34), chr(39))}"' if link.title else ""
    return f"[{_escape_markdown_label(link.text)}](<{url}>{title})"


def _inline_content(node: Tag) -> _InlineContent:
    """Return plain text, Markdown text, and safe links in source order."""

    segments: list[_InlineSegment] = []

    def walk(current: Tag | NavigableString) -> None:
        if isinstance(current, NavigableString):
            text = str(current)
            segments.append(_InlineSegment(text, text))
            return
        if not isinstance(current, Tag):
            return
        if current.name == "br":
            segments.append(_InlineSegment("\n", "\n"))
            return
        if current.name == "a":
            label = _clean_text(current.get_text(" ", strip=True))
            url = _safe_link_url(current.get("href"))
            if label and url:
                link = ReadingLink(label, url, _attribute(current, "title"))
                segments.append(_InlineSegment(label, _markdown_link(link), link))
                return
        for nested in current.children:
            if isinstance(nested, (Tag, NavigableString)):
                walk(nested)

    for child in node.children:
        if isinstance(child, (Tag, NavigableString)):
            walk(child)
    return _InlineContent(tuple(segments))


class ArticleReadingProjector:
    """Convert an article into ordered blocks without persisting derived data."""

    def project(self, article: Article) -> ArticleReadingProjection:
        digest = content_digest(article.content_html, article.content_text)
        drafts: list[dict[str, object]] = []
        if article.content_html:
            soup = BeautifulSoup(article.content_html, "html.parser")
            root = soup.body or soup
            self._walk(root, drafts)
        if not drafts:
            for paragraph in re.split(r"\n\s*\n|\n", article.content_text):
                self._append_text(drafts, "paragraph", paragraph)

        blocks = tuple(
            ReadingBlock(
                id=f"{digest}:{index:06d}",
                index=index,
                type=cast(str, draft["type"]),
                text=cast(str, draft.get("text", "")),
                level=cast(int | None, draft.get("level")),
                url=cast(str | None, draft.get("url")),
                alt=cast(str | None, draft.get("alt")),
                title=cast(str | None, draft.get("title")),
                caption=cast(str | None, draft.get("caption")),
                markdown=cast(str | None, draft.get("markdown")),
                links=cast(tuple[ReadingLink, ...], draft.get("links", ())),
            )
            for index, draft in enumerate(drafts)
        )
        return ArticleReadingProjection(
            article_id=str(article.id),
            content_hash=digest,
            blocks=blocks,
            sections=self._sections(blocks),
        )

    def _walk(self, node: Tag, drafts: list[dict[str, object]]) -> None:
        for child in node.children:
            if isinstance(child, NavigableString) or not isinstance(child, Tag):
                continue
            name = child.name.lower()
            if name in {"script", "style", "svg", "template"}:
                continue
            if name == "img":
                self._append_image(drafts, child)
                continue
            if re.fullmatch(r"h[1-6]", name):
                self._append_inline(
                    drafts,
                    "heading",
                    _inline_content(child),
                    level=int(name[1]),
                )
                continue
            if name in _ATOMIC_TAG_TYPES:
                block_type = _ATOMIC_TAG_TYPES[name]
                if name in {"li", "blockquote"}:
                    # WeChat's recommended-reading lists commonly use
                    # ``li > section > span > a``.  ``get_text`` would retain
                    # only the label and silently discard the destination.
                    self._append_inline(
                        drafts,
                        block_type,
                        _inline_content(child),
                    )
                else:
                    if name == "table":
                        self._append_inline(drafts, block_type, _table_content(child))
                    else:
                        self._append_text(drafts, block_type, child.get_text("\n", strip=True))
                for image in child.find_all("img"):
                    self._append_image(drafts, image)
                continue
            if name == "p":
                self._append_inline(drafts, "paragraph", _inline_content(child))
                for image in child.find_all("img"):
                    self._append_image(drafts, image)
                continue
            if name in {"figcaption"}:
                continue
            if (
                name in _CONTAINER_TAGS
                or child.find(
                    [
                        "p",
                        "img",
                        "blockquote",
                        "li",
                        "pre",
                        "table",
                        *[f"h{i}" for i in range(1, 7)],
                    ]
                )
                is not None
            ):
                self._walk(child, drafts)
                continue
            # WeChat frequently puts its end-of-article recommendation links in
            # standalone ``span`` elements rather than paragraphs.  Treat every
            # remaining leaf as inline content so a safe nested ``<a>`` keeps
            # both its Markdown target and structured link metadata.
            self._append_inline(drafts, "paragraph", _inline_content(child))

    @staticmethod
    def _append_inline(
        drafts: list[dict[str, object]],
        block_type: str,
        content: _InlineContent,
        *,
        level: int | None = None,
    ) -> None:
        """Split only text segments; never reconstruct Markdown from labels."""
        current: list[_InlineSegment] = []
        current_length = 0

        def flush() -> None:
            nonlocal current, current_length
            if not current:
                return
            text = _clean_text("".join(segment.text for segment in current))
            markdown = _clean_text("".join(segment.markdown for segment in current))
            if text or markdown:
                draft: dict[str, object] = {"type": block_type, "text": text, "markdown": markdown}
                if level is not None:
                    draft["level"] = level
                links = tuple(segment.link for segment in current if segment.link is not None)
                if links:
                    draft["links"] = links
                drafts.append(draft)
            current = []
            current_length = 0

        for segment in content.segments:
            if segment.link is not None or len(segment.text) <= _MAX_BLOCK_CHARS:
                pieces = [segment]
            else:
                pieces = [_InlineSegment(part, part) for part in _split_text(segment.text)]
            for piece in pieces:
                if current and current_length + len(piece.text) > _MAX_BLOCK_CHARS:
                    flush()
                current.append(piece)
                current_length += len(piece.text)
        flush()

    @staticmethod
    def _append_text(
        drafts: list[dict[str, object]],
        block_type: str,
        value: str,
        *,
        level: int | None = None,
    ) -> None:
        for part in _split_text(value):
            draft: dict[str, object] = {"type": block_type, "text": part}
            if level is not None:
                draft["level"] = level
            drafts.append(draft)

    @staticmethod
    def _append_image(drafts: list[dict[str, object]], image: Tag) -> None:
        url = _safe_image_url(image.get("src") or image.get("data-src"))
        if url is None:
            return
        figure = image.find_parent("figure")
        caption_tag = figure.find("figcaption") if figure is not None else None
        drafts.append(
            {
                "type": "image",
                "url": url,
                "alt": _attribute(image, "alt"),
                "title": _attribute(image, "title"),
                "caption": _clean_text(caption_tag.get_text(" ", strip=True))
                if caption_tag
                else None,
            }
        )

    @staticmethod
    def _sections(blocks: tuple[ReadingBlock, ...]) -> tuple[ReadingSection, ...]:
        headings = [block for block in blocks if block.type == "heading"]
        if not headings:
            return (ReadingSection("正文", 1, 0, len(blocks)),)
        sections: list[ReadingSection] = []
        if headings[0].index > 0:
            sections.append(ReadingSection("正文", 1, 0, headings[0].index))
        for position, heading in enumerate(headings):
            end = headings[position + 1].index if position + 1 < len(headings) else len(blocks)
            sections.append(ReadingSection(heading.text, heading.level or 1, heading.index, end))
        return tuple(sections)


def text_blocks(blocks: Iterable[ReadingBlock]) -> list[ReadingBlock]:
    return [block for block in blocks if block.type in _TEXT_BLOCK_TYPES and block.text]
