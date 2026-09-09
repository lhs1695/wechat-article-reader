"""微信公众号文章阅读与摘要服务

一个支持微信公众号文章抓取、SQLite 缓存、分页 Markdown 阅读与可选摘要导出的本地优先工具。

架构：
- 领域驱动设计 (DDD) + 六边形架构 (Hexagonal Architecture)
- 仅抓取微信公众号文章（HTTPX）
- 支持可选 DeepSeek 单次结构化摘要
- 支持 CLI、Web、MCP 共用的文章工作流与阅读投影

使用方式：
    python -m wechat_article_reader --help
    python -m wechat_article_reader web
"""

from .shared.constants import APP_NAME, VERSION

__version__ = VERSION
__app_name__ = APP_NAME

__all__ = ["__app_name__", "__version__"]
