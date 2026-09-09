"""微信公众号文章阅读服务：抓取 → SQLite → 统一 Markdown 投影 → 导出。

仅 mp.weixin.qq.com。摘要可选；CLI、Web、MCP 共用同一套阅读服务。

    python -m wechat_article_reader --help
    python -m wechat_article_reader web
"""

from .shared.constants import APP_NAME, VERSION

__version__ = VERSION
__app_name__ = APP_NAME

__all__ = ["__app_name__", "__version__"]
