"""文章仓储与工作单元端口。"""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self, runtime_checkable
from uuid import UUID

from ....domain.entities import Article


@runtime_checkable
class ArticleRepository(Protocol):
    """以领域实体为边界的文章仓储。"""

    def save(self, article: Article) -> None: ...

    def get(self, article_id: UUID) -> Article | None: ...

    def get_by_url(self, url: str) -> Article | None: ...

    def list_recent(self, limit: int = 20) -> list[Article]: ...

    def delete(self, article_id: UUID) -> bool: ...


@runtime_checkable
class UnitOfWork(Protocol):
    """一次短事务的边界。"""

    articles: ArticleRepository

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
