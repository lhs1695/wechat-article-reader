"""全局常量"""

import importlib.metadata as _metadata

# 版本信息 — 从 pyproject.toml 动态读取，保证唯一来源
try:
    VERSION = _metadata.version("wechat-article-reader")
except _metadata.PackageNotFoundError:
    VERSION = "3.1.0"  # fallback（开发模式未 pip install -e . 时）

APP_NAME = "WeChat Article Reader"

# 默认配置
DEFAULT_TIMEOUT = 30  # 秒
DEFAULT_MAX_RETRIES = 3
DEFAULT_CHUNK_SIZE = 4000  # Token

# User-Agent池
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
]

# 微信相关
WECHAT_DOMAIN = "mp.weixin.qq.com"
WECHAT_CONTENT_SELECTORS = [
    "#js_content",
    ".rich_media_content",
    "#page-content",
]

# LLM默认配置
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_SUMMARY_MAX_LENGTH = 1_200
DEFAULT_BATCH_SUMMARY_MAX_LENGTH = 300
DEFAULT_MCP_HTTP_PORT = 8_001

# Token限制
MAX_TOKENS_DEEPSEEK = 64000

# 主题颜色
THEME_COLORS = {
    "primary": "#07C160",  # 微信绿
    "secondary": "#576B95",  # 微信蓝
    "success": "#91d5ff",
    "warning": "#faad14",
    "error": "#ff4d4f",
    "background": "#f5f5f5",
    "text": "#333333",
}

# 文件路径
CONFIG_DIR_NAME = ".wechat_article_reader"
CONFIG_FILE_NAME = "config.yaml"
CACHE_DIR_NAME = "cache"
LOG_FILE_NAME = "app.log"
