"""兼容既有 StoragePort 的 SQLite 适配器。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select

from ...domain.entities import Article
from ...shared.exceptions import StorageError
from .database import Database
from .models import ArticleModel, SummaryModel
from .repository import normalize_article_url
from .unit_of_work import SqlAlchemyUnitOfWork


@dataclass(frozen=True)
class DatabaseStats:
    total_entries: int
    total_size_bytes: int
    summary_entries: int
    created_at_min: datetime | None
    created_at_max: datetime | None


class SqlAlchemyStorage:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save(self, article: Article) -> None:
        try:
            with self._uow() as uow:
                uow.articles.save(article)
                uow.commit()
        except Exception as exc:
            raise StorageError(f"保存失败: {exc}") from exc

    def get(self, article_id: UUID) -> Article | None:
        try:
            with self._uow() as uow:
                return uow.articles.get(article_id)
        except Exception as exc:
            raise StorageError(f"读取失败: {exc}") from exc

    def get_by_url(self, url: str) -> Article | None:
        try:
            with self._uow() as uow:
                return uow.articles.get_by_url(url)
        except Exception as exc:
            raise StorageError(f"读取失败: {exc}") from exc

    def list_recent(self, limit: int = 20) -> list[Article]:
        try:
            with self._uow() as uow:
                return uow.articles.list_recent(limit)
        except Exception as exc:
            raise StorageError(f"读取失败: {exc}") from exc

    def delete(self, article_id: UUID) -> bool:
        try:
            with self._uow() as uow:
                deleted = uow.articles.delete(article_id)
                uow.commit()
                return deleted
        except Exception as exc:
            raise StorageError(f"删除失败: {exc}") from exc

    def exists(self, url: str) -> bool:
        from ...domain.value_objects import ArticleURL

        normalized = normalize_article_url(str(ArticleURL.from_string(url)))
        with self.database.session_factory() as session:
            return bool(
                session.scalar(
                    select(func.count())
                    .select_from(ArticleModel)
                    .where(ArticleModel.normalized_url == normalized)
                )
            )

    def get_stats(self) -> DatabaseStats:
        with self.database.session_factory() as session:
            count = int(session.scalar(select(func.count()).select_from(ArticleModel)) or 0)
            summary_count = int(session.scalar(select(func.count()).select_from(SummaryModel)) or 0)
            created_at_min = session.scalar(select(func.min(ArticleModel.created_at)))
            created_at_max = session.scalar(select(func.max(ArticleModel.created_at)))
        size = Path(self.database.path).stat().st_size if Path(self.database.path).exists() else 0
        return DatabaseStats(
            total_entries=count,
            total_size_bytes=size,
            summary_entries=summary_count,
            created_at_min=created_at_min,
            created_at_max=created_at_max,
        )

    def clear_all(self) -> int:
        with self._uow() as uow:
            articles = uow.articles.list_recent(limit=2_147_483_647)
            for article in articles:
                uow.articles.delete(article.id)
            uow.commit()
            return len(articles)

    def close(self) -> None:
        self.database.dispose()

    def _uow(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self.database.session_factory)
