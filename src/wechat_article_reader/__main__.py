"""微信公众号文章阅读与摘要服务 - 主入口点

运行模式：
- Web（默认/无参数）：python -m wechat_article_reader
- CLI：python -m wechat_article_reader <command> [args]
"""

import sys

from .shared.utils import setup_logger


def main() -> None:
    if len(sys.argv) <= 1:
        setup_logger()
        _run_web()
        return

    # CLI 命令
    from .presentation.cli import run_cli

    run_cli()


def _run_web() -> None:
    from .infrastructure.config import get_settings

    _ = get_settings()  # side-effect: load .env config
    try:
        from .presentation.web import run

        run(host="127.0.0.1", port=8000)
    except ImportError as e:
        print(f"Web 启动失败: {e}")
        print("请安装: pip install fastapi uvicorn")
        sys.exit(1)


if __name__ == "__main__":
    main()
