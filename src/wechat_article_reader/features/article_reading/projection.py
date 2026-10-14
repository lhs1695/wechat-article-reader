"""Build a deterministic, model-friendly reading projection from article HTML."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from html import escape
from typing import Any, cast
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from ...domain.entities import Article
from .dto import ArticleReadingProjection, ReadingBlock, ReadingLink, ReadingSection

_CONTAINER_TAGS = {"article", "div", "main", "section"}
_TEXT_BLOCK_TYPES = {"heading", "paragraph", "list_item", "quote", "code", "table"}
_MAX_BLOCK_CHARS = 900
_ACCOUNT_FOOTER_MARKERS = frozenset({"今日好文推荐", "猜你喜欢"})


def content_digest(content_html: str, fallback_text: str = "") -> str:
    """Hash the delivery-relevant source, not just its extracted text."""
    source = content_html or fallback_text
    normalized = "\n".join(line.strip() for line in source.splitlines() if line.strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _clean_text(value: str) -> str:
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines())
    return "\n".join(line for line in lines if line)


def escape_markdown_text(value: str) -> str:
    """Keep source text literal; only real HTML links become Markdown links."""
    value = re.sub(r"&(?=(?:#\w+|[A-Za-z][A-Za-z0-9]+);)", "&amp;", value)
    value = re.sub(r"([\\`*_\[\]<>])", r"\\\1", value)
    return re.sub(
        r"(?m)^(\s*)(#{1,6}(?=\s)|[-+](?=\s)|\d+[.)](?=\s)|-{3,}|~{3,}|={3,})",
        lambda match: (
            match[1]
            + (match[2][:-1] + "\\" + match[2][-1] if match[2][0].isdigit() else "\\" + match[2])
        ),
        value,
    )


def _split_text(value: str, limit: int = _MAX_BLOCK_CHARS) -> list[str]:
    """Split plain source text without dropping spaces or cutting entities."""
    parts: list[str] = []
    start = 0
    while start < len(value):
        end = min(start + limit, len(value))
        ampersand = value.rfind("&", start, end)
        if ampersand >= 0:
            entity = re.match(r"&(?:#\w+|[A-Za-z][A-Za-z0-9]+);", value[ampersand:])
            if entity and ampersand + entity.end() > end:
                end = ampersand if ampersand > start else ampersand + entity.end()
        parts.append(value[start:end])
        start = end
    return parts


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
    atomic: bool = False


@dataclass(frozen=True)
class _InlineContent:
    segments: tuple[_InlineSegment, ...]

    @property
    def text(self) -> str:
        return _clean_text("".join(segment.text for segment in self.segments))

    @property
    def markdown(self) -> str:
        return "".join(segment.markdown for segment in self.segments).strip()

    @property
    def links(self) -> tuple[ReadingLink, ...]:
        return tuple(segment.link for segment in self.segments if segment.link is not None)


def _table_content(table: Tag) -> _InlineContent:
    text_rows: list[list[str]] = []
    markdown_rows: list[list[str]] = []
    links: list[ReadingLink] = []
    rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
    complex_table = table.find("table") is not None or any(
        str(cell.get(name, "1")) != "1"
        for cell in table.find_all(["th", "td"])
        for name in ("rowspan", "colspan")
    )
    for row in rows:
        text_cells: list[str] = []
        markdown_cells: list[str] = []
        for cell in row.find_all(["th", "td"], recursive=False):
            content = _inline_content(cell)
            text_cells.append(content.text.replace("|", "\\|").replace("\n", " / "))
            markdown_cells.append(
                content.markdown.replace("|", "\\|").replace("  \n", "<br>").replace("\n", "<br>")
            )
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
    if rows and rows[0].find("th", recursive=False) is None:
        header = [f"列 {index + 1}" for index in range(width)]
        text_rows.insert(0, header)
        markdown_rows.insert(0, header)

    def render(rows: list[list[str]]) -> str:
        return "\n".join(
            [f"| {' | '.join(rows[0])} |", f"| {' | '.join(['---'] * width)} |"]
            + [f"| {' | '.join(row)} |" for row in rows[1:]]
        )

    markdown = render(markdown_rows)
    if complex_table:
        from ...shared.utils.html_safety import sanitize_html

        markdown = sanitize_html(str(table))
    return _InlineContent(
        (
            _InlineSegment(render(text_rows), markdown, atomic=True),
            *(_InlineSegment("", "", link) for link in links),
        )
    )


def _escape_markdown_label(value: str) -> str:
    return escape_markdown_text(re.sub(r"\s+", " ", value))


def _markdown_link(link: ReadingLink) -> str:
    url = markdown_destination(link.url)
    title = markdown_title(link.title)
    return f"[{_escape_markdown_label(link.text)}](<{url}>{title})"


def markdown_destination(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="/:?#[]@!$&'()*+,;=%-._~")


def markdown_title(value: str | None) -> str:
    if not value:
        return ""
    title = re.sub(r"\s+", " ", value).replace("\\", "\\\\").replace('"', '\\"')
    return f' "{title}"'


def code_fence(value: str, minimum: int = 3) -> str:
    return "`" * max(minimum, max((len(run) + 1 for run in re.findall(r"`+", value)), default=0))


def _code_text(node: Tag) -> str:
    """Preserve whitespace and token boundaries in syntax-highlighted HTML."""
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            if child.name == "br":
                parts.append("\n")
            else:
                if child.name in {"div", "p"} and parts and not parts[-1].endswith("\n"):
                    parts.append("\n")
                parts.append(_code_text(child))
                if child.name in {"div", "p"} and not parts[-1].endswith("\n"):
                    parts.append("\n")
    return "".join(parts).replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")


def _inline_content(node: Tag, *, include_self: bool = False) -> _InlineContent:
    """Return plain text, Markdown text, and safe links in source order."""

    segments: list[_InlineSegment] = []

    def walk(current: Tag | NavigableString) -> None:
        if isinstance(current, Comment):
            return
        if isinstance(current, NavigableString):
            text = re.sub(r"[\t\r\n ]+", " ", str(current))
            segments.append(_InlineSegment(text, escape_markdown_text(text)))
            return
        if not isinstance(current, Tag):
            return
        if current.name == "br":
            segments.append(_InlineSegment("\n", "  \n", atomic=True))
            return
        if current.name == "code":
            text = _code_text(current).replace("\n", " ")
            fence = code_fence(text, minimum=1)
            padding = (
                " "
                if text.startswith("`")
                or text.endswith("`")
                or (text.startswith(" ") and text.endswith(" ") and text.strip())
                else ""
            )
            segments.append(
                _InlineSegment(text, f"{fence}{padding}{text}{padding}{fence}", atomic=True)
            )
            return
        if current.name in {"strong", "b", "em", "i", "del", "sub", "sup"}:
            content = _inline_content(current)
            text = "".join(segment.text for segment in content.segments)
            raw_markdown = "".join(segment.markdown for segment in content.segments)
            markdown = raw_markdown.strip()
            marker = {"strong": "**", "b": "**", "em": "*", "i": "*", "del": "~~"}.get(current.name)
            if marker:
                leading = raw_markdown[: len(raw_markdown) - len(raw_markdown.lstrip())]
                trailing = raw_markdown[len(raw_markdown.rstrip()) :]
                markdown = (
                    f"{leading}{marker}{markdown}{marker}{trailing}" if markdown else raw_markdown
                )
            else:
                markdown = f"<{current.name}>{escape(text)}</{current.name}>"
            segments.append(_InlineSegment(text, markdown, atomic=True))
            segments.extend(_InlineSegment("", "", link) for link in content.links)
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
        if current.name in {"p", "div", "section"}:
            segments.append(_InlineSegment("\n", "  \n", atomic=True))

    if include_self:
        walk(node)
    else:
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
            drafts = self._drop_trailing_account_footer(drafts)
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
                language=cast(str | None, draft.get("language")),
                list_marker=cast(str | None, draft.get("list_marker")),
                children=cast(tuple[ReadingBlock, ...], draft.get("children", ())),
                continues_previous=bool(draft.get("continues_previous", False)),
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
        pending: list[_InlineSegment] = []
        group_inline = any(
            isinstance(child, NavigableString)
            and not isinstance(child, Comment)
            and str(child).strip()
            for child in node.children
        )

        def flush_inline() -> None:
            if pending:
                self._append_inline(drafts, "paragraph", _InlineContent(tuple(pending)))
                pending.clear()

        for child in node.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                if str(child).strip():
                    text = re.sub(r"[\t\r\n ]+", " ", str(child))
                    pending.append(_InlineSegment(text, escape_markdown_text(text)))
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name.lower()
            if name in {"script", "style", "svg", "template"}:
                continue
            if (
                group_inline
                and name
                in {"span", "strong", "em", "b", "i", "del", "a", "code", "sub", "sup", "br", "u"}
                and child.find(["p", "pre", "ul", "ol", "table", "section", "div"]) is None
            ):
                pending.extend(_inline_content(child, include_self=True).segments)
                continue
            flush_inline()
            if name == "img":
                self._append_image(drafts, child)
                continue
            if name == "hr":
                drafts.append({"type": "rule", "text": "", "markdown": "---"})
                continue
            if name in {"ul", "ol"}:
                try:
                    start = int(str(child.get("start", "1")))
                except ValueError:
                    start = 1
                for offset, item in enumerate(child.find_all("li", recursive=False)):
                    marker = f"{max(0, start + offset)}." if name == "ol" else "-"
                    self._append_container(drafts, "list_item", item, marker=marker)
                continue
            if name in {"li", "blockquote"}:
                self._append_container(
                    drafts, "quote" if name == "blockquote" else "list_item", child
                )
                continue
            if name == "pre":
                code = child.find("code")
                classes = [
                    str(value)
                    for tag in (child, code)
                    if tag is not None
                    for value in cast(list[str], tag.get("class") or [])
                ]
                language = next(
                    (
                        value.removeprefix("language-")
                        for value in classes
                        if value.startswith("language-")
                    ),
                    None,
                )
                language = language or str(
                    child.get("data-lang") or (code.get("data-lang") if code else "") or ""
                )
                if not re.fullmatch(r"[A-Za-z0-9_+-]+", language or ""):
                    language = None
                drafts.append({"type": "code", "text": _code_text(child), "language": language})
                continue
            if re.fullmatch(r"h[1-6]", name):
                self._append_inline(
                    drafts,
                    "heading",
                    _inline_content(child),
                    level=int(name[1]),
                )
                continue
            if name == "table":
                content = _table_content(child)
                self._append_inline(drafts, "table", content)
                if not content.markdown.lstrip().startswith("<table"):
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
            self._append_inline(drafts, "paragraph", _inline_content(child, include_self=True))
        flush_inline()

    def _append_container(
        self,
        drafts: list[dict[str, object]],
        block_type: str,
        node: Tag,
        *,
        marker: str | None = None,
    ) -> None:
        from .renderer import MarkdownReadingRenderer

        nested: list[dict[str, object]] = []
        # Plain inline list items remain one paragraph; nested lists and block
        # content retain their own nodes and indentation.
        if (
            node.find(
                [
                    "p",
                    "ul",
                    "ol",
                    "pre",
                    "blockquote",
                    "table",
                    "section",
                    "div",
                    "img",
                    *[f"h{i}" for i in range(1, 7)],
                ]
            )
            is None
        ):
            self._append_inline(nested, "paragraph", _inline_content(node))
        else:
            self._walk(node, nested)
        children = tuple(
            ReadingBlock(id="", index=index, **cast(dict[str, Any], draft))
            for index, draft in enumerate(nested)
        )
        drafts.append(
            {
                "type": block_type,
                "text": "\n\n".join(block.text for block in children if block.text),
                "markdown": MarkdownReadingRenderer().render(children),
                "children": children,
                "list_marker": marker or ("-" if block_type == "list_item" else None),
                "links": tuple(link for block in children for link in block.links),
            }
        )

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
        continuing = False

        def flush() -> None:
            nonlocal current, current_length, continuing
            if not current:
                return
            text = _clean_text("".join(segment.text for segment in current))
            markdown = "".join(segment.markdown for segment in current)
            if text or markdown:
                draft: dict[str, object] = {"type": block_type, "text": text, "markdown": markdown}
                draft["continues_previous"] = continuing
                if level is not None:
                    draft["level"] = level
                links = tuple(segment.link for segment in current if segment.link is not None)
                if links:
                    draft["links"] = links
                drafts.append(draft)
                continuing = True
            current = []
            current_length = 0

        for segment in content.segments:
            if segment.atomic or segment.link is not None or len(segment.text) <= _MAX_BLOCK_CHARS:
                pieces = [segment]
            else:
                pieces = [
                    _InlineSegment(part, escape_markdown_text(part))
                    for part in _split_text(segment.text)
                ]
            for piece in pieces:
                if current and piece.text and current_length + len(piece.text) > _MAX_BLOCK_CHARS:
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
        ArticleReadingProjector._append_inline(
            drafts,
            block_type,
            _InlineContent((_InlineSegment(value, escape_markdown_text(value)),)),
            level=level,
        )

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
    def _drop_trailing_account_footer(drafts: list[dict[str, object]]) -> list[dict[str, object]]:
        """Drop account recommend-walls after an explicit footer marker.

        In-article lists under a real heading (for example 延伸阅读) stay.
        """
        cut: int | None = None
        for index, draft in enumerate(drafts):
            if draft["type"] == "heading":
                continue
            if _clean_text(str(draft.get("text") or "")) in _ACCOUNT_FOOTER_MARKERS:
                tail = drafts[index + 1 :]
                # Require an actual short recommendation wall, not a prose
                # mention or a real section sharing the same title.
                linked = [item for item in tail if item.get("links")]
                if (
                    len(linked) >= 2
                    and len(tail) >= 2
                    and all(
                        item.get("links")
                        and sum(
                            len(link.text) for link in cast(tuple[ReadingLink, ...], item["links"])
                        )
                        >= 0.7 * len(str(item.get("text", "")))
                        for item in tail[:2]
                    )
                    and not any(item["type"] == "heading" for item in tail)
                    and sum(len(str(item.get("text", ""))) for item in tail) <= 1500
                ):
                    cut = index
        if cut is None:
            return drafts
        return drafts[:cut]

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


def image_count(blocks: Iterable[ReadingBlock]) -> int:
    return sum(
        (1 if block.type == "image" else 0)
        + image_count(block.children)
        + (
            len(BeautifulSoup(block.markdown, "html.parser").find_all("img"))
            if block.type == "table"
            and block.markdown
            and block.markdown.lstrip().startswith("<table")
            else 0
        )
        for block in blocks
    )
