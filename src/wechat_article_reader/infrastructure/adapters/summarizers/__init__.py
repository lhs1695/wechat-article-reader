"""摘要器适配器"""

from .base import BaseSummarizer
from .deepseek import DeepSeekSummarizer

__all__ = [
    "BaseSummarizer",
    "DeepSeekSummarizer",
]
