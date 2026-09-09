"""摘要器基类"""

from abc import ABC, abstractmethod
from threading import Event

from ....domain.entities import Summary
from ....domain.value_objects import ArticleContent
from ....shared.constants import DEFAULT_SUMMARY_MAX_LENGTH


class BaseSummarizer(ABC):
    """摘要器抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """摘要器名称"""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """检查是否可用"""
        pass

    @abstractmethod
    def summarize(
        self,
        content: ArticleContent,
        max_length: int = DEFAULT_SUMMARY_MAX_LENGTH,
        *,
        cancel_event: Event | None = None,
    ) -> Summary:
        """生成摘要"""
        pass
