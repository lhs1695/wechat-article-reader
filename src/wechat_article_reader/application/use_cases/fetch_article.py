"""抓取文章用例"""

from __future__ import annotations

from threading import Event
from typing import TYPE_CHECKING

from loguru import logger

from ...domain.entities import Article
from ...domain.value_objects import ArticleURL
from ...shared.exceptions import (
    ArticlePersistenceError,
    InvalidURLError,
    OperationCancelledError,
    OperationDeadlineExceededError,
    ScraperError,
    UseCaseError,
)
from ...shared.operation import BoundedKeyedLockPool, check_operation_deadline

if TYPE_CHECKING:
    from ..ports.outbound import ScraperPort, StoragePort


class FetchArticleUseCase:
    """
    抓取文章用例

    负责协调抓取器来获取文章内容。
    """

    def __init__(
        self,
        scrapers: list[ScraperPort],
        storage: StoragePort | None = None,
    ):
        """
        Args:
            scrapers: 抓取器列表（按优先级排序）
            storage: 可选存储（用于缓存已抓取文章）
        """
        self._scrapers = scrapers
        self._storage = storage
        self._inflight = BoundedKeyedLockPool(max_keys=256)

    def execute(
        self,
        url: str,
        preferred_scraper: str | None = None,
        *,
        cancel_event: Event | None = None,
        force_refresh: bool = False,
    ) -> Article:
        """
        执行抓取文章用例

        Args:
            url: 文章URL
            preferred_scraper: 指定使用的抓取器名称（可选）
            force_refresh: 跳过已有缓存并重新抓取

        Returns:
            抓取到的文章实体

        Raises:
            UseCaseError: 抓取失败
        """
        try:
            article_url = ArticleURL.from_string(url)
        except InvalidURLError:
            raise
        except Exception as e:
            raise InvalidURLError("无效的URL") from e

        if not article_url.is_wechat:
            logger.warning("抓取失败 kind=not_wechat host={}", article_url.domain)
            raise InvalidURLError("不是有效的微信公众号链接")

        normalized_url = str(article_url)
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelledError()
        check_operation_deadline(cancel_event)

        cached_before_refresh = None
        if force_refresh and self._storage is not None:
            try:
                cached_before_refresh = self._storage.get_by_url(normalized_url)
            except Exception as exc:
                logger.warning("缓存读取失败，刷新将继续 error_type={}", type(exc).__name__)

        with self._inflight.acquire(normalized_url, cancel_event):
            check_operation_deadline(cancel_event)
            if self._storage is not None:
                try:
                    cached = self._storage.get_by_url(normalized_url)
                    if cached is not None and not force_refresh:
                        logger.info(f"缓存命中: {normalized_url}")
                        return cached
                    if (
                        cached is not None
                        and force_refresh
                        and (
                            cached_before_refresh is None
                            or cached.updated_at != cached_before_refresh.updated_at
                        )
                    ):
                        return cached
                except Exception as e:
                    logger.warning(f"缓存读取失败，忽略: {e}")

            return self._fetch_and_save(article_url, preferred_scraper, cancel_event)

    def _fetch_and_save(
        self,
        article_url: ArticleURL,
        preferred_scraper: str | None,
        cancel_event: Event | None,
    ) -> Article:
        # 如果指定了抓取器，优先使用
        if preferred_scraper:
            for scraper in self._scrapers:
                if scraper.name == preferred_scraper:
                    article = self._scrape_with(scraper, article_url, cancel_event)
                    self._save_cache_if_needed(article, cancel_event)
                    return article
            logger.warning(f"未找到指定的抓取器: {preferred_scraper}")

        last_scraper_error: ScraperError | None = None
        for scraper in self._scrapers:
            if scraper.can_handle(article_url):
                try:
                    article = self._scrape_with(scraper, article_url, cancel_event)
                    self._save_cache_if_needed(article, cancel_event)
                    return article
                except ScraperError as e:
                    last_scraper_error = e
                    logger.warning(
                        "抓取失败 scraper={} error_type={} code={}",
                        scraper.name,
                        type(e).__name__,
                        e.code,
                    )
                    continue

        if last_scraper_error is not None:
            raise last_scraper_error
        raise UseCaseError("没有可用的抓取器能处理该公众号链接")

    def _scrape_with(
        self, scraper: ScraperPort, url: ArticleURL, cancel_event: Event | None
    ) -> Article:
        """使用指定抓取器抓取"""
        logger.info(f"使用 {scraper.name} 抓取: {url}")

        try:
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelledError()
            check_operation_deadline(cancel_event)
            article = scraper.scrape(url, cancel_event=cancel_event)
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelledError()
            check_operation_deadline(cancel_event)
            logger.info(f"抓取成功: {article.title} ({article.word_count}字)")
            return article
        except (OperationCancelledError, OperationDeadlineExceededError, ScraperError):
            # 保留 ScraperError 子类信息（如 ScraperBlockedError）
            raise
        except Exception as e:
            logger.error("抓取失败 error_type={}", type(e).__name__)
            raise ScraperError("抓取失败") from e

    def _save_cache_if_needed(self, article: Article, cancel_event: Event | None = None) -> None:
        if self._storage is None:
            return

        try:
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelledError()
            check_operation_deadline(cancel_event)
            self._storage.save(
                article, **({"cancel_event": cancel_event} if cancel_event is not None else {})
            )
        except (OperationCancelledError, OperationDeadlineExceededError):
            raise
        except Exception as e:
            logger.warning("缓存写入失败 error_type={}", type(e).__name__)
            raise ArticlePersistenceError(
                "文章抓取成功，但写入 SQLite 缓存失败；请检查数据库状态后重试"
            ) from e
