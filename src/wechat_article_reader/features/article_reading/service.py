"""Stable cached reading workflows for external and local agents."""

from __future__ import annotations

from collections import OrderedDict
from threading import Event, RLock
from typing import TYPE_CHECKING
from uuid import UUID

from ...shared.exceptions import ArticleVersionChangedError, OperationCancelledError
from ...shared.utils.article_identity import normalize_section_title
from .dto import (
    ArticleExportResult,
    ArticleReadingMetadata,
    ArticleReadingProjection,
    ArticleReadPage,
    IngestResult,
    ReadingBlock,
    ReadingSection,
)
from .projection import ArticleReadingProjector, content_digest, escape_markdown_text, image_count
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


class SectionSelectionError(ValueError):
    pass


class ArticleReadingService:
    def __init__(self, workflow: ArticleWorkflowService, storage: StoragePort | None) -> None:
        self._workflow = workflow
        self._storage = storage
        self._projector = ArticleReadingProjector()
        self._renderer = MarkdownReadingRenderer()
        self._projection_cache: OrderedDict[
            tuple[str, str, str, str], tuple[ArticleReadingProjection, int]
        ] = OrderedDict()
        self._projection_lock = RLock()
        self._projection_bytes = 0
        self._projection_max_entries = 16
        self._projection_max_bytes = 8 * 1024 * 1024

    def _project(self, article: Article) -> ArticleReadingProjection:
        key = (
            str(article.id),
            article.revision,
            content_digest(article.content_html, article.content_text),
            str(article.url),
        )
        with self._projection_lock:
            if key in self._projection_cache:
                self._projection_cache.move_to_end(key)
                return self._projection_cache[key][0]
            projection = self._projector.project(article)

            def weight(block: ReadingBlock) -> int:
                return (
                    256
                    + sum(
                        len(value.encode("utf-8"))
                        for value in (block.text, block.markdown or "", block.url or "")
                    )
                    + sum(128 + len(link.url.encode("utf-8")) for link in block.links)
                    + sum(weight(child) for child in block.children)
                )

            size = sum(weight(block) for block in projection.blocks)
            if size <= self._projection_max_bytes:
                for stale in [old for old in self._projection_cache if old[0] == key[0]]:
                    self._projection_bytes -= self._projection_cache.pop(stale)[1]
                self._projection_cache[key] = (projection, size)
                self._projection_bytes += size
                while (
                    len(self._projection_cache) > self._projection_max_entries
                    or self._projection_bytes > self._projection_max_bytes
                ):
                    self._projection_bytes -= self._projection_cache.popitem(last=False)[1][1]
            return projection

    def export_markdown(
        self,
        article_id: str | UUID,
        *,
        path: str,
        include_images: bool = False,
        expected_revision: str | None = None,
        cancel_event: Event | None = None,
    ) -> ArticleExportResult:
        if not isinstance(include_images, bool):
            raise ValueError("include_images must be a boolean")
        article = self._get_article(article_id)
        _check_cancelled(cancel_event)
        if expected_revision is not None and expected_revision != article.revision:
            raise ArticleVersionChangedError(article.revision)
        export_options = {"cancel_event": cancel_event} if cancel_event is not None else {}
        export_path = self._workflow.export_existing(
            article,
            target="markdown",
            path=path,
            include_body=True,
            include_images=include_images,
            **export_options,
        )
        return ArticleExportResult(
            article_id=str(article.id),
            title=article.title,
            word_count=article.word_count,
            export_path=export_path,
            revision=article.revision,
        )

    def ingest(
        self,
        url: str,
        *,
        refresh: bool = False,
        toc_level: int = DEFAULT_TOC_LEVEL,
        cancel_event: Event | None = None,
    ) -> IngestResult:
        storage = self._require_storage()
        _check_cancelled(cancel_event)
        existing = storage.get_by_url(url)
        cached = existing is not None and not refresh
        if cached:
            persisted = existing
        else:
            article = self._workflow.fetch_article(
                url,
                force_refresh=refresh,
                **({"cancel_event": cancel_event} if cancel_event is not None else {}),
            )
            try:
                _check_cancelled(cancel_event)
                current = storage.get(article.id)
                if current is None:
                    storage.save(
                        article,
                        **({"cancel_event": cancel_event} if cancel_event is not None else {}),
                    )
                persisted = storage.get(article.id)
            except OperationCancelledError:
                raise
            except Exception as exc:
                raise ArticleStorageError("文章持久化失败") from exc
        if persisted is None:
            raise ArticleStorageError("文章持久化后无法读取")
        _check_cancelled(cancel_event)
        projection = self._project(persisted)
        return IngestResult(
            article_id=str(persisted.id),
            title=persisted.title,
            word_count=persisted.word_count,
            image_count=image_count(projection.blocks),
            cached=cached,
            sections=_sections_for_toc(projection.sections, toc_level, len(projection.blocks)),
            block_count=len(projection.blocks),
            revision=persisted.revision,
            content_notes=projection.notes,
        )

    def get_metadata(
        self,
        article_id: str | UUID,
        *,
        toc_level: int = DEFAULT_TOC_LEVEL,
    ) -> ArticleReadingMetadata:
        article = self._get_article(article_id)
        projection = self._project(article)
        return ArticleReadingMetadata(
            article_id=str(article.id),
            url=str(article.url),
            title=article.title,
            author=article.author,
            account_name=article.account_name,
            publish_time=article.publish_time_str,
            word_count=article.word_count,
            image_count=image_count(projection.blocks),
            fetched_at=article.source.scraped_at.isoformat() if article.source else None,
            sections=_sections_for_toc(projection.sections, toc_level, len(projection.blocks)),
            block_count=len(projection.blocks),
            revision=article.revision,
            first_cached_at=article.created_at.isoformat(),
            summary_generated_at=article.summary.created_at.isoformat()
            if article.summary
            else None,
            summary_generation=article.summary.generation if article.summary else None,
            content_notes=projection.notes,
        )

    def read(
        self,
        article_id: str | UUID,
        *,
        cursor: int = 0,
        max_chars: int = DEFAULT_READ_MAX_CHARS,
        include_images: bool = False,
        section: str | None = None,
        section_start_cursor: int | None = None,
        expected_revision: str | None = None,
        cancel_event: Event | None = None,
    ) -> ArticleReadPage:
        if type(cursor) is not int or cursor < 0:
            raise ValueError("cursor must be non-negative")
        if type(max_chars) is not int or not 1_000 <= max_chars <= 20_000:
            raise ValueError("max_chars must be in [1000, 20000]")
        if not isinstance(include_images, bool):
            raise ValueError("include_images must be a boolean")
        article = self._get_article(article_id)
        _check_cancelled(cancel_event)
        if cursor > 0 and expected_revision is None:
            raise ValueError("续读必须传 expected_revision；请原样使用上一页 next_read")
        if expected_revision is not None and expected_revision != article.revision:
            raise ArticleVersionChangedError(article.revision)
        projection = self._project(article)
        window_end = len(projection.blocks)
        matched = None
        if section is not None and section_start_cursor is not None:
            raise ValueError("section 与 section_start_cursor 只能选择一个")
        if section_start_cursor is not None:
            if type(section_start_cursor) is not int:
                raise ValueError("section_start_cursor must be an integer")
            matched = next(
                (item for item in projection.sections if item.start_cursor == section_start_cursor),
                None,
            )
            if matched is None:
                raise _section_error("section_start_cursor 不存在", projection.sections)
        elif section is not None:
            matched = resolve_section(projection.sections, section)
        if matched is not None:
            if cursor == 0:
                cursor = matched.start_cursor
            elif cursor < matched.start_cursor or cursor > matched.end_cursor:
                raise ValueError("cursor 不在所选章节内；请使用该章节的 next_read")
            window_end = matched.end_cursor
        if cursor > len(projection.blocks):
            raise ValueError("cursor exceeds article block count")

        selected, next_cursor = self._select_page_blocks(
            projection.blocks,
            cursor=cursor,
            max_chars=max_chars,
            include_images=include_images,
            stop_before=window_end,
        )

        def page_content(blocks: list[ReadingBlock]) -> tuple[str, str]:
            body = self._renderer.render(blocks, include_images=include_images)
            preamble = _page_preamble(
                article,
                cursor,
                blocks,
                _page_section_title(_covering_section(projection.sections, cursor), blocks),
                projection.sections,
            )
            return body, f"{preamble}\n\n{body}".strip() if preamble else body

        body, content = page_content(selected)
        while len(content) > max_chars and len(selected) > 1:
            next_cursor = selected.pop().index
            body, content = page_content(selected)
        if next_cursor < window_end:
            selected, next_cursor = _drop_trailing_heading(selected, next_cursor)
            body, content = page_content(selected)
        if len(content) > 20_000:
            raise ArticlePageSizeError(
                f"含文头的完整块有 {len(content)} 字，超过 20000；请 export_article 导出完整文件"
            )
        _check_cancelled(cancel_event)
        has_more = next_cursor < window_end
        covering = _covering_section(projection.sections, cursor)
        section_title = _page_section_title(covering, selected)
        page_images = image_count(projection.blocks[cursor:next_cursor])
        notes = list(projection.notes)
        if page_images:
            notes.append(
                f"本页含 {page_images} 张图片，{'仅返回链接和说明' if include_images else '图片已省略'}；未读取图片中的内容"
            )
        next_read: dict[str, object] | None = None
        if has_more:
            next_read = {
                "article_id": str(article.id),
                "cursor": next_cursor,
                "expected_revision": article.revision,
                "max_chars": max_chars,
                "include_images": include_images,
            }
            if matched is not None:
                next_read["section_start_cursor"] = matched.start_cursor
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
            section_end_cursor=matched.end_cursor
            if matched is not None
            else (covering.end_cursor if covering else None),
            revision=article.revision,
            sections_in_page=tuple(
                item
                for item in projection.sections
                if item.start_cursor < next_cursor and item.end_cursor > cursor
            ),
            image_count=page_images,
            omitted_images=page_images if not include_images else 0,
            content_notes=tuple(notes),
            next_read=next_read,
            reading_scope="section" if matched is not None else "article",
            end_of_article=next_cursor >= len(projection.blocks),
        )

    def _select_page_blocks(
        self,
        blocks: tuple[ReadingBlock, ...],
        *,
        cursor: int,
        max_chars: int,
        include_images: bool,
        stop_before: int,
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
                while (
                    look < total
                    and blocks[look].type == "list_item"
                    and blocks[look].list_group == block.list_group
                ):
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

        if next_cursor < total:
            selected, next_cursor = _drop_trailing_heading(selected, next_cursor)
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
    real_levels = [section.level for section in sections if not section.synthetic]
    if real_levels:
        toc_level = max(toc_level, min(real_levels))
    visible = [section for section in sections if section.level <= toc_level]
    if not visible:
        return (ReadingSection("正文", 1, 0, block_count),)
    return tuple(visible)


def resolve_section(sections: tuple[ReadingSection, ...], section: str) -> ReadingSection:
    needle = normalize_section_title(section)
    if not needle:
        raise ValueError("section must be a non-empty string")
    exact = [item for item in sections if item.title == needle]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise _section_error(f"section {needle!r} is ambiguous", tuple(exact))
    if len(needle) < _MIN_SECTION_PREFIX:
        raise _section_error(f"section not found: {needle}", sections)
    prefixes = [item for item in sections if item.title.startswith(needle)]
    if len(prefixes) == 1:
        return prefixes[0]
    if len(prefixes) > 1:
        raise _section_error(f"section {needle!r} is ambiguous", tuple(prefixes))
    raise _section_error(f"section not found: {needle}", sections)


def resolve_section_cursor(sections: tuple[ReadingSection, ...], section: str) -> int:
    return resolve_section(sections, section).start_cursor


def _covering_section(sections: tuple[ReadingSection, ...], cursor: int) -> ReadingSection | None:
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
            return f"作者：{escape_markdown_text(author)}" if author else ""
        heading = escape_markdown_text(f"{title} · {author}" if author else title)
        return f"# {heading}"
    if not section_title or any(
        item.synthetic and item.start_cursor <= cursor < item.end_cursor for item in sections
    ):
        return ""
    first = selected[0] if selected else None
    if first is not None and first.type == "heading" and first.text == section_title:
        return ""
    level = 2
    for item in sections:
        if item.title == section_title:
            level = max(1, min(item.level or 2, 6))
            break
    return f"{'#' * level} {escape_markdown_text(section_title)}"


def _section_error(message: str, sections: tuple[ReadingSection, ...]) -> SectionSelectionError:
    import json

    choices = [
        {"title": item.title[:100], "section_start_cursor": item.start_cursor}
        for item in sections[:10]
    ]
    return SectionSelectionError(message + "; 可用章节: " + json.dumps(choices, ensure_ascii=False))


def _check_cancelled(event: Event | None) -> None:
    if event is not None and event.is_set():
        raise OperationCancelledError()


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
