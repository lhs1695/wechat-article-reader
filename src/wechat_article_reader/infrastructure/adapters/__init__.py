"""基础设施适配器"""

from .exporters import (
    BaseExporter,
    HtmlExporter,
    MarkdownExporter,
)
from .http_client_pool import ClientConfig, HttpClientPool, get_async_client, get_http_pool
from .scrapers import BaseScraper, WechatHttpxScraper
from .summarizers import (
    BaseSummarizer,
    DeepSeekSummarizer,
)

__all__ = [
    "BaseExporter",
    "BaseScraper",
    "BaseSummarizer",
    "ClientConfig",
    "DeepSeekSummarizer",
    "HtmlExporter",
    "HttpClientPool",
    "MarkdownExporter",
    "WechatHttpxScraper",
    "get_async_client",
    "get_http_pool",
]
