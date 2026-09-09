"""Stable cached reading workflows for external and local agents."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from .dto import (
    ArticleReadingMetadata,
    ArticleReadPage,
    IngestResult,
    ReadingBlock,
    ReadingSection,
)
from .projection import ArticleReadingProjector
from .renderer import MarkdownReadingRenderer

if TYPE_CHECKING:
    from ...application.ports.outbound.storage_port import StoragePort
    from ...domain.entities import Article
    from ..article_workflow import ArticleWorkflowService

DEFAULT_TOC_LEVEL = 2
DEFAULT_READ_MAX_CHARS = 20_000
_HEADING_BREAK_MAX_LEVEL = 3
_MIN_SECTION_PREFIX = 2


class ArticleReadingError(RuntimeError):
    pass


class ArticleNotFoundError(ArticleReadingError):
    pass


class ArticleStorageError(ArticleReadingError):
    pass


class ArticlePageSizeError(ArticleReadingError):
    pass


class ArticleReadingService:
    def __init__(self, workflow: ArticleWorkflowService, storage: StoragePort | None) -> None:
        self._workflow = workflow
        self._storage = storage
        self._projector = ArticleReadingProjector()
        self._renderer = MarkdownReadingRenderer()

    def ingest(
        self,
        url: str,
        *,
        refresh: bool = False,
        toc_level: int = DEFAULT_TOC_LEVEL,
    ) -> IngestResult:
        storage = self._require_storage()
        existing = storage.get_by_url(url)
        cached = existing is not None and not refresh
        if cached:
            persisted = existing
        else:
            article = self._workflow.fetch_article(url, force_refresh=refresh)
            try:
                storage.save(article)
                persisted = storage.get(article.id)
            except Exception as exc:
                raise ArticleStorageError("文章持久化失败") from exc
        if persisted is None:
            raise ArticleStorageError("文章持久化后无法读取")
        projection = self._projector.project(persisted)
        return IngestResult(
            article_id=str(persisted.id),
            title=persisted.title,
            word_count=persisted.word_count,
            image_count=sum(block.type == "image" for block in projection.blocks),
            cached=cached,
            sections=_sections_for_toc(projection.sections, toc_level, len(projection.blocks)),
            block_count=len(projection.blocks),
        )

    def get_metadata(
        self,
        article_id: str | UUID,
        *,
        toc_level: int = DEFAULT_TOC_LEVEL,
    ) -> ArticleReadingMetadata:
        article = self._get_article(article_id)
        projection = self._projector.project(article)
        return ArticleReadingMetadata(
            article_id=str(article.id),
            url=str(article.url),
            title=article.title,
            author=article.author,
            account_name=article.account_name,
            publish_time=article.publish_time_str,
            word_count=article.word_count,
            image_count=sum(block.type == "image" for block in projection.blocks),
            fetched_at=article.source.scraped_at.isoformat() if article.source else None,
            sections=_sections_for_toc(projection.sections, toc_level, len(projection.blocks)),
            block_count=len(projection.blocks),
        )

    def read(
        self,
        article_id: str | UUID,
        *,
        cursor: int = 0,
        max_chars: int = DEFAULT_READ_MAX_CHARS,
        include_images: bool = False,
        section: str | None = None,
    ) -> ArticleReadPage:
        if cursor < 0:
            raise ValueError("cursor must be non-negative")
        if not 1_000 <= max_chars <= 20_000:
            raise ValueError("max_chars must be in [1000, 20000]")
        if not isinstance(include_images, bool):
            raise ValueError("include_images must be a boolean")
        article = self._get_article(article_id)
        projection = self._projector.project(article)
        window_end = len(projection.blocks)
        if section is not None:
            matched = resolve_section(projection.sections, section)
            if cursor < matched.start_cursor or cursor >= matched.end_cursor:
                cursor = matched.start_cursor
            window_end = matched.end_cursor
        if cursor > len(projection.blocks):
            raise ValueError("cursor exceeds article block count")

        selected, next_cursor = self._select_page_blocks(
            projection.blocks,
            cursor=cursor,
            max_chars=max_chars,
            include_images=include_images,
            stop_before=window_end,
            drop_section_teasers=section is not None,
        )
        has_more = next_cursor < window_end
        covering = _covering_section(projection.sections, cursor)
        section_title = _page_section_title(covering, selected)
        body = self._renderer.render(selected, include_images=include_images)
        preamble = _page_preamble(article, cursor, selected, section_title, projection.sections)
        content = f"{preamble}\n\n{body}".strip() if preamble else body
        return ArticleReadPage(
            article_id=str(article.id),
            cursor=cursor,
            next_cursor=next_cursor if has_more else None,
            has_more=has_more,
            content_markdown=content,
            block_count=len(projection.blocks),
            word_count=len(body),
            section_title=section_title,
            chars=len(content),
            section_end_cursor=covering.end_cursor if covering is not None else None,
        )

    def _select_page_blocks(
        self,
        blocks: tuple[ReadingBlock, ...],
        *,
        cursor: int,
        max_chars: int,
        include_images: bool,
        stop_before: int,
        drop_section_teasers: bool = False,
    ) -> tuple[list[ReadingBlock], int]:
        selected: list[ReadingBlock] = []
        rendered_length = 0
        next_cursor = cursor
        index = cursor
        total = min(len(blocks), stop_before)

        def rendered(block: ReadingBlock) -> str:
            return self._renderer.render_block(block, include_images=include_images)

        def join_cost(chunk: str, *, nonempty: bool) -> int:
            return len(chunk) + (2 if nonempty else 0)

        while index < total:
            block = blocks[index]
            text = rendered(block)
            if not text:
                next_cursor = block.index + 1
                index += 1
                continue

            if block.type == "list_item":
                group: list[tuple[ReadingBlock, str]] = [(block, text)]
                look = index + 1
                while look < total and blocks[look].type == "list_item":
                    item_text = rendered(blocks[look])
                    if item_text:
                        group.append((blocks[look], item_text))
                    look += 1
                group_len = 0
                for offset, (_, item_text) in enumerate(group):
                    group_len += join_cost(item_text, nonempty=bool(selected) or offset > 0)
                if selected and rendered_length + group_len > max_chars:
                    break
                if not selected and group_len > max_chars:
                    for item, item_text in group:
                        added = join_cost(item_text, nonempty=bool(selected))
                        if not selected and added > max_chars:
                            if added > 20_000:
                                raise ArticlePageSizeError(
                                    f"下一个完整 Markdown 块有 {added} 字，超过 20000 上限，无法分页返回"
                                )
                            selected.append(item)
                            return selected, item.index + 1
                        if selected and rendered_length + added > max_chars:
                            return selected, item.index
                        selected.append(item)
                        rendered_length += added
                    return selected, group[-1][0].index + 1
                for item, item_text in group:
                    selected.append(item)
                    rendered_length += join_cost(item_text, nonempty=rendered_length > 0)
                next_cursor = look
                index = look
                continue

            added = join_cost(text, nonempty=bool(selected))
            if not selected and added > max_chars:
                if added > 20_000:
                    raise ArticlePageSizeError(
                        f"下一个完整 Markdown 块有 {added} 字，超过 20000 上限，无法分页返回"
                    )
                selected.append(block)
                next_cursor = block.index + 1
                break
            if selected and rendered_length + added > max_chars:
                break
            selected.append(block)
            rendered_length += added
            next_cursor = block.index + 1
            index += 1

        selected, next_cursor = _drop_trailing_heading(selected, next_cursor)
        if drop_section_teasers and next_cursor >= stop_before:
            selected = _drop_trailing_section_teasers(blocks, selected, stop_before)
        return selected, next_cursor

    def _get_article(self, article_id: str | UUID):
        storage = self._require_storage()
        try:
            normalized_id = article_id if isinstance(article_id, UUID) else UUID(str(article_id))
        except (TypeError, ValueError) as exc:
            raise ValueError("article_id must be a UUID") from exc
        article = storage.get(normalized_id)
        if article is None:
            raise ArticleNotFoundError(f"文章不存在: {normalized_id}")
        return article

    def _require_storage(self) -> StoragePort:
        if self._storage is None:
            raise ArticleStorageError("文章阅读需要 SQLite 存储")
        return self._storage


def _sections_for_toc(
    sections: tuple[ReadingSection, ...],
    toc_level: int,
    block_count: int,
) -> tuple[ReadingSection, ...]:
    if not 1 <= toc_level <= 6:
        raise ValueError("toc_level must be in [1, 6]")
    real_levels = [section.level for section in sections if section.title != "正文"]
    if real_levels:
        toc_level = max(toc_level, min(real_levels))
    visible = [section for section in sections if section.level <= toc_level]
    if not visible:
        return (ReadingSection("正文", 1, 0, block_count),)
    filtered: list[ReadingSection] = []
    for position, section in enumerate(visible):
        end = visible[position + 1].start_cursor if position + 1 < len(visible) else block_count
        filtered.append(ReadingSection(section.title, section.level, section.start_cursor, end))
    return tuple(filtered)


def resolve_section(sections: tuple[ReadingSection, ...], section: str) -> ReadingSection:
    needle = section.strip()
    if not needle:
        raise ValueError("section must be a non-empty string")
    exact = [item for item in sections if item.title == needle]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ValueError(f"section {needle!r} is ambiguous")
    if len(needle) < _MIN_SECTION_PREFIX:
        raise ValueError(f"section not found: {needle}")
    prefixes = [item for item in sections if item.title.startswith(needle)]
    if len(prefixes) == 1:
        return prefixes[0]
    if len(prefixes) > 1:
        raise ValueError(f"section {needle!r} is ambiguous")
    raise ValueError(f"section not found: {needle}")


def resolve_section_cursor(sections: tuple[ReadingSection, ...], section: str) -> int:
    return resolve_section(sections, section).start_cursor


def _covering_section(
    sections: tuple[ReadingSection, ...], cursor: int
) -> ReadingSection | None:
    covering = [item for item in sections if item.start_cursor <= cursor < item.end_cursor]
    if covering:
        covering.sort(key=lambda item: (item.start_cursor, item.level))
        return covering[-1]
    if sections and cursor >= sections[-1].start_cursor:
        return sections[-1]
    return None


def _page_section_title(
    covering: ReadingSection | None,
    selected: list[ReadingBlock],
) -> str | None:
    if covering is not None:
        return covering.title
    for block in selected:
        if block.type == "heading" and block.text:
            return block.text
    return None


def _page_preamble(
    article: Article,
    cursor: int,
    selected: list[ReadingBlock],
    section_title: str | None,
    sections: tuple[ReadingSection, ...],
) -> str:
    if cursor == 0:
        title = (article.title or "").strip() or "未命名"
        author = (article.author or "").strip()
        first = selected[0] if selected else None
        first_heading = (
            first.text.strip()
            if first is not None and first.type == "heading" and first.text
            else ""
        )
        if first_heading == title:
            return f"作者：{author}" if author else ""
        return f"# {title} · {author}" if author else f"# {title}"
    if not section_title or section_title == "正文":
        return ""
    first = selected[0] if selected else None
    if first is not None and first.type == "heading" and first.text == section_title:
        return ""
    level = 2
    for item in sections:
        if item.title == section_title:
            level = max(1, min(item.level or 2, 6))
            break
    return f"{'#' * level} {section_title}"


def _drop_trailing_heading(
    selected: list[ReadingBlock], next_cursor: int
) -> tuple[list[ReadingBlock], int]:
    """Keep H2/H3 with their body: a heading with no following block on this page starts the next."""
    if len(selected) < 2:
        return selected, next_cursor
    last = selected[-1]
    if last.type == "heading" and (last.level or 1) <= _HEADING_BREAK_MAX_LEVEL:
        return selected[:-1], last.index
    return selected, next_cursor


def _is_section_teaser(block: ReadingBlock) -> bool:
    """WeChat template chrome that announces the next heading (05 / PHASE THREE)."""
    if block.type != "paragraph" or block.links:
        return False
    text = (block.text or "").strip()
    if not text or len(text) > 40:
        return False
    if text.isdigit() and len(text) <= 2:
        return True
    if text == "∞":
        return True
    compact = text.replace(" ", "").replace("-", "")
    return compact.isascii() and compact.isupper() and compact.isalnum() and 2 <= len(compact) <= 28


def _drop_trailing_section_teasers(
    blocks: tuple[ReadingBlock, ...],
    selected: list[ReadingBlock],
    stop_before: int,
) -> list[ReadingBlock]:
    if stop_before >= len(blocks) or blocks[stop_before].type != "heading":
        return selected
    trimmed = list(selected)
    while len(trimmed) > 1 and _is_section_teaser(trimmed[-1]):
        trimmed.pop()
    return trimmed
