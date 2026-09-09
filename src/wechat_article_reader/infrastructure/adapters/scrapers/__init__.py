"""抓取器适配器"""

from .base import BaseScraper
from .wechat_httpx import WechatHttpxScraper

__all__ = [
    "BaseScraper",
    "WechatHttpxScraper",
]
