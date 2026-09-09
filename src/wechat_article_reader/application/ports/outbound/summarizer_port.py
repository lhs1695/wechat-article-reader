"""摘要器出站端口 - 定义摘要器适配器必须实现的接口"""

from threading import Event
from typing import Protocol, runtime_checkable

from ....domain.entities import Summary
from ....domain.value_objects import ArticleContent
from ....shared.constants import DEFAULT_SUMMARY_MAX_LENGTH


@runtime_checkable
class SummarizerPort(Protocol):
    """
    摘要器端口

    定义摘要器适配器必须实现的接口。
    基础设施层的具体摘要器实现此接口。
    """

    @property
    def name(self) -> str:
        """摘要器名称"""
        ...

    def is_available(self) -> bool:
        """
        检查摘要器是否可用

        Returns:
            是否可用（如API是否配置、服务是否在线等）
        """
        ...

    def summarize(
        self,
        content: ArticleContent,
        max_length: int = DEFAULT_SUMMARY_MAX_LENGTH,
        *,
        cancel_event: Event | None = None,
    ) -> Summary:
        """
        生成摘要

        Args:
            content: 文章内容
            max_length: 最大字数

        Returns:
            生成的摘要

        Raises:
            SummarizerError: 摘要生成失败
        """
        ...
