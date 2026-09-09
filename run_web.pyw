#!/usr/bin/env python
"""微信文章阅读 Web 启动器。

使用 pythonw.exe 运行此文件，可在不显示控制台窗口的情况下启动服务。
"""

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_PATH = PROJECT_ROOT / "src"

if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


def _restart_in_project_venv() -> None:
    project_pythonw = PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if not project_pythonw.is_file():
        return
    if Path(sys.executable).resolve() == project_pythonw.resolve():
        return

    os.execv(
        str(project_pythonw),
        [str(project_pythonw), str(Path(__file__).resolve())],
    )


def main() -> None:
    import uvicorn

    uvicorn.run(
        "wechat_article_reader.presentation.web.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
        log_level="info",
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    _restart_in_project_venv()
    try:
        main()
    except Exception as exc:
        log_path = PROJECT_ROOT / "error.log"
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"\n[{datetime.now()}] 启动错误: {exc}\n")
            log_file.write(traceback.format_exc())
            log_file.write("\n")
        raise SystemExit(1) from exc
