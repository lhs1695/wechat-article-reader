"""抓取文章用例"""

from __future__ import annotations

from threading import Event
from typing import TYPE_CHECKING

from loguru import logger

from ...domain.entities import Article
from ...domain.value_objects import ArticleURL
from ...shared.exceptions import (
    InvalidURLError,
    OperationCancelledError,
    ScraperError,
    UseCaseError,
)

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
            raise InvalidURLError(f"无效的URL: {e}") from e

        normalized_url = str(article_url)

        # 缓存命中
        if self._storage is not None and not force_refresh:
            try:
                cached = self._storage.get_by_url(normalized_url)
                if cached is not None:
                    logger.info(f"缓存命中: {normalized_url}")
                    return cached
            except Exception as e:
                logger.warning(f"缓存读取失败，忽略: {e}")

        # 如果指定了抓取器，优先使用
        if preferred_scraper:
            for scraper in self._scrapers:
                if scraper.name == preferred_scraper:
                    article = self._scrape_with(scraper, article_url, cancel_event)
                    self._save_cache_if_needed(article)
                    return article
            logger.warning(f"未找到指定的抓取器: {preferred_scraper}")

        last_scraper_error: ScraperError | None = None
        for scraper in self._scrapers:
            if scraper.can_handle(article_url):
                try:
                    article = self._scrape_with(scraper, article_url, cancel_event)
                    self._save_cache_if_needed(article)
                    return article
                except ScraperError as e:
                    last_scraper_error = e
                    logger.warning(f"抓取器 {scraper.name} 失败: {e}, 尝试下一个...")
                    continue

        if last_scraper_error is not None:
            raise last_scraper_error
        raise UseCaseError(f"没有可用的抓取器能处理URL: {url}")

    def _scrape_with(
        self, scraper: ScraperPort, url: ArticleURL, cancel_event: Event | None
    ) -> Article:
        """使用指定抓取器抓取"""
        logger.info(f"使用 {scraper.name} 抓取: {url}")

        try:
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelledError()
            article = scraper.scrape(url, cancel_event=cancel_event)
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelledError()
            logger.info(f"抓取成功: {article.title} ({article.word_count}字)")
            return article
        except (OperationCancelledError, ScraperError):
            # 保留 ScraperError 子类信息（如 ScraperBlockedError）
            raise
        except Exception as e:
            logger.error(f"抓取失败: {e}")
            raise ScraperError(f"抓取失败: {e}") from e

    def _save_cache_if_needed(self, article: Article) -> None:
        if self._storage is None:
            return

        try:
            self._storage.save(article)
        except Exception as e:
            logger.warning(f"缓存写入失败，忽略: {e}")
