"""依赖注入容器 - 组装应用组件"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self

from loguru import logger

from ...application.use_cases import (
    BatchProcessUseCase,
    ExportArticleUseCase,
    FetchArticleUseCase,
    SummarizeArticleUseCase,
)
from ...features.article_reading import ArticleReadingService
from ...features.article_workflow import ArticleWorkflowService
from .assembly import (
    build_exporters,
    build_scrapers,
    build_storage,
    build_summarizers,
)
from .settings import AppSettings, get_settings

if TYPE_CHECKING:
    from ...application.ports.outbound import (
        ExporterPort,
        ScraperPort,
        StoragePort,
        SummarizerPort,
    )


@dataclass
class Container:
    """
    依赖注入容器

    负责创建和管理应用程序的所有依赖项。
    负责组装抓取、缓存、阅读、摘要与导出组件。
    """

    settings: AppSettings = field(default_factory=get_settings)

    # 线程安全锁（保护懒加载属性的初始化）
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    # 适配器缓存
    _scrapers: list[ScraperPort] | None = field(default=None, init=False)
    _summarizers: dict[str, SummarizerPort] | None = field(default=None, init=False)
    _exporters: dict[str, ExporterPort] | None = field(default=None, init=False)
    _storage: StoragePort | None = field(default=None, init=False)

    # 用例缓存
    _fetch_use_case: FetchArticleUseCase | None = field(default=None, init=False)
    _summarize_use_case: SummarizeArticleUseCase | None = field(default=None, init=False)
    _export_use_case: ExportArticleUseCase | None = field(default=None, init=False)
    _batch_use_case: BatchProcessUseCase | None = field(default=None, init=False)
    _article_workflow_service: ArticleWorkflowService | None = field(default=None, init=False)
    _article_reading_service: ArticleReadingService | None = field(default=None, init=False)
    _minimal: bool = field(default=False, init=False, repr=False)

    @classmethod
    def create_minimal(cls) -> Container:
        """创建最小化容器（用于测试环境）

        不连接外部服务、不加载大型模型，避免测试套件挂起。
        先走完整 __init__ 再用空集合覆盖各组件字段。
        """
        instance = cls()
        instance._scrapers = []
        instance._summarizers = {}
        instance._exporters = {}
        instance._storage = None
        instance._fetch_use_case = None
        instance._summarize_use_case = None
        instance._export_use_case = None
        instance._batch_use_case = None
        instance._article_workflow_service = None
        instance._article_reading_service = None
        instance._minimal = True
        return instance

    @property
    def scrapers(self) -> list[ScraperPort]:
        """获取抓取器列表"""
        if self._scrapers is None:
            with self._lock:
                if self._scrapers is None:
                    self._scrapers = self._create_scrapers()
        return self._scrapers

    @property
    def summarizers(self) -> dict[str, SummarizerPort]:
        """获取摘要器字典"""
        if self._summarizers is None:
            with self._lock:
                if self._summarizers is None:
                    self._summarizers = self._create_summarizers()
        return self._summarizers

    @property
    def exporters(self) -> dict[str, ExporterPort]:
        """获取导出器字典"""
        if self._exporters is None:
            with self._lock:
                if self._exporters is None:
                    self._exporters = self._create_exporters()
        return self._exporters

    @property
    def storage(self) -> StoragePort | None:
        """获取存储适配器（用于缓存，可选）"""
        if self._minimal:
            return None
        if self._storage is None:
            with self._lock:
                if self._storage is None:
                    self._storage = self._create_storage()
        return self._storage

    @property
    def fetch_use_case(self) -> FetchArticleUseCase:
        """获取抓取文章用例"""
        if self._fetch_use_case is None:
            with self._lock:
                if self._fetch_use_case is None:
                    self._fetch_use_case = FetchArticleUseCase(self.scrapers, storage=self.storage)
        return self._fetch_use_case

    @property
    def summarize_use_case(self) -> SummarizeArticleUseCase:
        """获取摘要用例"""
        if self._summarize_use_case is None:
            with self._lock:
                if self._summarize_use_case is None:
                    self._summarize_use_case = SummarizeArticleUseCase(
                        self.summarizers.get("deepseek"),
                        max_input_chars=self.settings.deepseek.max_input_chars,
                    )
        return self._summarize_use_case

    @property
    def export_use_case(self) -> ExportArticleUseCase:
        """获取导出用例"""
        if self._export_use_case is None:
            with self._lock:
                if self._export_use_case is None:
                    self._export_use_case = ExportArticleUseCase(self.exporters)
        return self._export_use_case

    @property
    def batch_use_case(self) -> BatchProcessUseCase:
        """获取批量处理用例"""
        if self._batch_use_case is None:
            with self._lock:
                if self._batch_use_case is None:
                    self._batch_use_case = BatchProcessUseCase(
                        fetch_use_case=self.fetch_use_case,
                        summarize_use_case=self.summarize_use_case,
                        export_use_case=self.export_use_case,
                    )
        return self._batch_use_case

    @property
    def article_workflow_service(self) -> ArticleWorkflowService:
        """Expose a feature-oriented service for article workflows."""
        if self._article_workflow_service is None:
            with self._lock:
                if self._article_workflow_service is None:
                    self._article_workflow_service = ArticleWorkflowService(
                        fetch_use_case=self.fetch_use_case,
                        summarize_use_case=self.summarize_use_case,
                        export_use_case=self.export_use_case,
                        storage=self.storage,
                    )
        return self._article_workflow_service

    @property
    def article_reading_service(self) -> ArticleReadingService:
        """Expose cached, paginated Markdown reading workflows."""
        if self._article_reading_service is None:
            with self._lock:
                if self._article_reading_service is None:
                    self._article_reading_service = ArticleReadingService(
                        self.article_workflow_service,
                        self.storage,
                    )
        return self._article_reading_service

    def _create_scrapers(self) -> list[ScraperPort]:
        """创建抓取器列表。"""
        return build_scrapers(self.settings)

    def _create_summarizers(
        self, extra_api_keys: dict[str, str] | None = None
    ) -> dict[str, SummarizerPort]:
        """创建摘要器字典。"""
        return build_summarizers(
            settings=self.settings,
            extra_api_keys=extra_api_keys,
        )

    # ==================== 生命周期管理 ====================

    async def __aenter__(self) -> Self:
        """异步上下文管理器入口"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """异步上下文管理器出口，自动清理资源"""
        await self.async_close()

    async def async_close(self) -> None:
        """异步关闭容器持有的所有资源

        遍历所有适配器，调用其 aclose/close 方法。
        支持同步和异步关闭方法。
        """
        all_resources: list[Any] = []
        # 收集所有需要关闭的资源
        all_resources.extend(self._scrapers or [])
        all_resources.extend((self._summarizers or {}).values())
        all_resources.extend((self._exporters or {}).values())
        if self._storage is not None:
            all_resources.append(self._storage)

        for resource in all_resources:
            try:
                aclose_fn = getattr(resource, "aclose", None)
                if callable(aclose_fn):
                    await aclose_fn()
                else:
                    close_fn = getattr(resource, "close", None)
                    if callable(close_fn):
                        result = close_fn()
                        if asyncio.iscoroutine(result):
                            await result
            except Exception as e:
                logger.debug(f"关闭资源 {type(resource).__name__} 失败: {e}")

        logger.debug("容器异步资源已关闭")

    def close(self) -> None:
        """同步关闭容器持有的资源（httpx 客户端、向量存储等）"""
        for scraper in self._scrapers or []:
            close_fn = getattr(scraper, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except Exception as e:
                    logger.debug(f"关闭 scraper 失败: {e}")

        for summarizer in (self._summarizers or {}).values():
            close_fn = getattr(summarizer, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except Exception as e:
                    logger.debug(f"关闭 summarizer 失败: {e}")

        if self._storage is not None:
            close_fn = getattr(self._storage, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except Exception as e:
                    logger.debug(f"关闭 storage 失败: {e}")

        logger.debug("容器资源已关闭")

    def reload_summarizers(self, api_keys: dict[str, str]) -> None:
        """重新加载摘要器（用于配置 API 密钥后刷新运行时）

        Args:
            api_keys: API 密钥字典 {"openai": "sk-...", "deepseek": "sk-...", ...}
        """
        self._summarizers = self._create_summarizers(extra_api_keys=api_keys)
        # 重建依赖 summarizers 的用例
        self._summarize_use_case = None
        self._batch_use_case = None
        self._article_workflow_service = None
        self._article_reading_service = None
        logger.info(f"摘要器已重新加载，当前可用: {list(self._summarizers.keys())}")

    def _create_storage(self) -> StoragePort | None:
        """创建默认 SQLite 存储适配器。"""
        return build_storage()

    def _create_exporters(self) -> dict[str, ExporterPort]:
        """创建导出器字典。"""
        return build_exporters(self.settings)


# 全局容器实例
_container: Container | None = None


def get_container() -> Container:
    """获取全局容器实例"""
    global _container
    if _container is None:
        _container = Container()
    return _container


def reset_container() -> None:
    """重置容器（用于测试）"""
    global _container
    if _container is not None:
        _container.close()
    _container = None
