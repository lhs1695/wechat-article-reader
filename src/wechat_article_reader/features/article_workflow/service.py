"""Feature service for the compact fetch, summarize and export workflow."""

from __future__ import annotations

from threading import Event
from typing import TYPE_CHECKING
from uuid import UUID

from ...shared.constants import DEFAULT_BATCH_SUMMARY_MAX_LENGTH, DEFAULT_SUMMARY_MAX_LENGTH
from .dto import (
    ArticleExportPayload,
    ArticleFetchPayload,
    ArticleInfoPayload,
    ArticleMetadataPayload,
    ArticleProcessPayload,
    ArticleSummaryPayload,
    BatchExportItemPayload,
    BatchExportPayload,
    BatchProcessItemPayload,
    BatchProcessPayload,
    BatchSummaryItemPayload,
    BatchSummaryPayload,
    SummaryPayload,
)

if TYPE_CHECKING:
    from ...application.ports.outbound.storage_port import StoragePort
    from ...application.use_cases.export_article import ExportArticleUseCase
    from ...application.use_cases.fetch_article import FetchArticleUseCase
    from ...application.use_cases.summarize_article import SummarizeArticleUseCase
    from ...domain.entities import Article, Summary


class ArticleWorkflowService:
    def __init__(
        self,
        fetch_use_case: FetchArticleUseCase,
        summarize_use_case: SummarizeArticleUseCase,
        export_use_case: ExportArticleUseCase | None = None,
        storage: StoragePort | None = None,
    ) -> None:
        self._fetch_use_case, self._summarize_use_case = fetch_use_case, summarize_use_case
        self._export_use_case, self._storage = export_use_case, storage

    def fetch(self, url: str, content_limit: int | None = 10_000) -> ArticleFetchPayload:
        article = self.fetch_article(url)
        content = article.content_text
        truncated = content_limit is not None and len(content) > content_limit
        return ArticleFetchPayload(
            **self._to_metadata(article).__dict__,
            overview=content[:content_limit] if truncated else content,
            content_html=article.content_html,
            content_truncated=truncated,
        )

    def fetch_article(
        self, url: str, *, cancel_event: Event | None = None, force_refresh: bool = False
    ) -> Article:
        if cancel_event is None and not force_refresh:
            return self._fetch_use_case.execute(url)
        return self._fetch_use_case.execute(
            url, cancel_event=cancel_event, force_refresh=force_refresh
        )

    def get_info(self, url: str, preview_limit: int = 500) -> ArticleInfoPayload:
        article = self.fetch_article(url)
        preview = article.content_text[:preview_limit]
        if len(article.content_text) > preview_limit:
            preview += "..."
        return ArticleInfoPayload(**self._to_metadata(article).__dict__, preview=preview)

    def process(
        self,
        url: str,
        *,
        max_length: int = DEFAULT_SUMMARY_MAX_LENGTH,
        cancel_event: Event | None = None,
        summarize: bool = True,
        target: str | None = None,
        path: str | None = None,
        include_body: bool = True,
        include_images: bool | None = None,
        summary_content: str = "",
        key_points: list[str] | tuple[str, ...] = (),
        tags: list[str] | tuple[str, ...] = (),
        continue_on_summary_error: bool = False,
    ) -> ArticleProcessPayload:
        if target is not None and self._export_use_case is None:
            raise RuntimeError("导出工作流不可用")
        article = self.fetch_article(url, cancel_event=cancel_event)
        summary = None
        summary_error = None
        if summary_content:
            from ...domain.entities import Summary

            summary = Summary(
                overview=summary_content, key_points=tuple(key_points), tags=tuple(tags)
            )
            article.attach_summary(summary)
        elif summarize:
            try:
                summary = self._summarize_use_case.execute(
                    article, max_length=max_length, cancel_event=cancel_event
                )
                article.attach_summary(summary)
            except Exception as exc:
                if not continue_on_summary_error:
                    raise
                summary_error = str(exc)
        if summary is not None and self._storage is not None:
            self._storage.save(article)
        export_path = None
        if target is not None:
            assert self._export_use_case is not None
            export_options: dict[str, bool] = {"include_body": include_body}
            if include_images is not None:
                export_options["include_images"] = include_images
            export_path = self._export_use_case.execute(
                article, target=target, path=path, **export_options
            )
        return ArticleProcessPayload(
            article=self._to_metadata(article),
            content=article.content_text,
            content_html=article.content_html,
            summary=self._to_summary(summary) if summary else None,
            export_path=export_path,
            summary_error=summary_error,
        )

    def summarize(
        self,
        url: str,
        max_length: int = DEFAULT_SUMMARY_MAX_LENGTH,
        *,
        cancel_event: Event | None = None,
    ) -> ArticleSummaryPayload:
        payload = self.process(url, max_length=max_length, cancel_event=cancel_event)
        assert payload.summary is not None
        return ArticleSummaryPayload(article=payload.article, summary=payload.summary)

    def summarize_existing(
        self,
        article: Article,
        *,
        max_length: int = DEFAULT_SUMMARY_MAX_LENGTH,
        cancel_event: Event | None = None,
    ) -> ArticleSummaryPayload:
        summary = self._summarize_use_case.execute(
            article, max_length=max_length, cancel_event=cancel_event
        )
        article.attach_summary(summary)
        if self._storage is not None:
            self._storage.save(article)
        return ArticleSummaryPayload(
            article=self._to_metadata(article), summary=self._to_summary(summary)
        )

    def summarize_cached(
        self,
        article_id: str | UUID,
        max_length: int = DEFAULT_SUMMARY_MAX_LENGTH,
        *,
        cancel_event: Event | None = None,
    ) -> ArticleSummaryPayload:
        return self.summarize_existing(
            self._require_cached_article(article_id),
            max_length=max_length,
            cancel_event=cancel_event,
        )

    def batch_summarize_cached(
        self,
        article_ids: list[str],
        max_length: int = DEFAULT_BATCH_SUMMARY_MAX_LENGTH,
        *,
        cancel_event: Event | None = None,
    ) -> BatchSummaryPayload:
        results = []
        for article_id in article_ids:
            try:
                payload = self.summarize_cached(
                    article_id, max_length=max_length, cancel_event=cancel_event
                )
                results.append(
                    BatchSummaryItemPayload(
                        url=payload.article.url,
                        success=True,
                        title=payload.article.title,
                        summary=payload.summary.overview,
                        key_points=payload.summary.key_points,
                        tags=payload.summary.tags,
                        article_id=article_id,
                    )
                )
            except Exception as exc:
                results.append(
                    BatchSummaryItemPayload(
                        url="",
                        success=False,
                        error=str(exc),
                        article_id=article_id,
                    )
                )
        return BatchSummaryPayload(len(article_ids), len(results), tuple(results))

    def batch_process(
        self,
        urls: list[str],
        *,
        max_length: int = DEFAULT_BATCH_SUMMARY_MAX_LENGTH,
        summarize: bool = True,
        target: str | None = None,
        path: str | None = None,
        include_body: bool = True,
        continue_on_summary_error: bool = False,
    ) -> BatchProcessPayload:
        results = []
        for url in urls:
            try:
                results.append(
                    BatchProcessItemPayload(
                        url,
                        True,
                        result=self.process(
                            url,
                            max_length=max_length,
                            summarize=summarize,
                            target=target,
                            path=path,
                            include_body=include_body,
                            continue_on_summary_error=continue_on_summary_error,
                        ),
                    )
                )
            except Exception as exc:
                results.append(BatchProcessItemPayload(url, False, error=str(exc)))
        return BatchProcessPayload(len(urls), len(results), tuple(results))

    def batch_summarize(
        self, urls: list[str], max_length: int = 300, *, cancel_event: Event | None = None
    ) -> BatchSummaryPayload:
        results = []
        for url in urls:
            try:
                payload = self.summarize(url, max_length=max_length, cancel_event=cancel_event)
                results.append(
                    BatchSummaryItemPayload(
                        url,
                        True,
                        payload.article.title,
                        payload.summary.overview,
                        payload.summary.key_points,
                        payload.summary.tags,
                    )
                )
            except Exception as exc:
                results.append(BatchSummaryItemPayload(url, False, error=str(exc)))
        return BatchSummaryPayload(len(urls), len(results), tuple(results))

    def export(
        self,
        url: str,
        *,
        target: str = "markdown",
        skip_summary: bool = False,
        summary_content: str = "",
        key_points: list[str] | tuple[str, ...] = (),
        include_body: bool = True,
        include_images: bool | None = None,
        path: str | None = None,
        tags: list[str] | tuple[str, ...] = (),
    ) -> ArticleExportPayload:
        payload = self.process(
            url,
            summarize=not skip_summary,
            target=target,
            path=path,
            include_body=include_body,
            include_images=include_images,
            summary_content=summary_content,
            key_points=key_points,
            tags=tags,
        )
        return ArticleExportPayload(
            payload.article, payload.export_path or "", payload.summary is not None
        )

    def export_existing(
        self, article: Article, *, target: str, path: str | None = None, include_body: bool = True
    ) -> str:
        if self._export_use_case is None:
            raise RuntimeError("导出工作流不可用")
        return self._export_use_case.execute(
            article, target=target, path=path, include_body=include_body
        )

    def batch_export(
        self,
        urls: list[str],
        *,
        target: str = "markdown",
        skip_summary: bool = False,
        include_body: bool = True,
    ) -> BatchExportPayload:
        results = []
        for url in urls:
            try:
                payload = self.export(
                    url,
                    target=target,
                    skip_summary=skip_summary,
                    include_body=include_body,
                    include_images=target != "markdown",
                )
                results.append(
                    BatchExportItemPayload(url, True, payload.article.title, payload.export_path)
                )
            except Exception as exc:
                results.append(BatchExportItemPayload(url, False, error=str(exc)))
        return BatchExportPayload(len(urls), len(results), tuple(results))

    def _require_cached_article(self, article_id: str | UUID):
        from ..article_reading import ArticleNotFoundError, ArticleStorageError

        if self._storage is None:
            raise ArticleStorageError("文章阅读需要 SQLite 存储")
        try:
            normalized_id = article_id if isinstance(article_id, UUID) else UUID(str(article_id))
        except (TypeError, ValueError) as exc:
            raise ValueError("article_id must be a UUID") from exc
        article = self._storage.get(normalized_id)
        if article is None:
            raise ArticleNotFoundError(f"文章不存在: {normalized_id}")
        return article

    @staticmethod
    def _to_metadata(article: Article) -> ArticleMetadataPayload:
        return ArticleMetadataPayload(
            str(article.url),
            article.title,
            article.author,
            article.account_name,
            article.publish_time_str,
            article.word_count,
        )

    @staticmethod
    def _to_summary(summary: Summary) -> SummaryPayload:
        return SummaryPayload(
            summary.overview, summary.key_points, summary.tags, summary.one_sentence
        )
