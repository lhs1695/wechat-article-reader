#!/usr/bin/env python
"""微信文章阅读 Web 启动器。

使用 pythonw.exe 运行此文件，可在不显示控制台窗口的情况下启动服务。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_PATH = PROJECT_ROOT / "src"
HOST = "127.0.0.1"
PORT = 8000
HOME_URL = f"http://{HOST}:{PORT}"

if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


def _ensure_stdio() -> None:
    """pythonw 下 stdout/stderr 为 None，uvicorn / loguru 写入会直接失败。"""

    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _write_error(exc: BaseException) -> None:
    log_path = PROJECT_ROOT / "error.log"
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"\n[{datetime.now()}] 启动错误: {exc}\n")
        log_file.write(traceback.format_exc())
        log_file.write("\n")


def _notify_failure(message: str) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "微信文章阅读", 0x10)
    except Exception:
        return


def _project_pythonw() -> Path | None:
    candidate = PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe"
    return candidate if candidate.is_file() else None


def _in_project_venv() -> bool:
    venv_root = (PROJECT_ROOT / ".venv").resolve()
    try:
        if Path(sys.prefix).resolve() == venv_root:
            return True
    except OSError:
        pass
    try:
        if Path(sys.executable).resolve().is_relative_to(venv_root):
            return True
    except (OSError, ValueError):
        pass
    return False


def _restart_in_project_venv() -> None:
    project_pythonw = _project_pythonw()
    if project_pythonw is None:
        return
    if _in_project_venv():
        return

    # Windows 上 os.execv 等价于 spawn(P_WAIT)：父进程一直等到子进程退出，
    # 双击看起来像卡住，且会留下两个 pythonw。改为分离后立刻退出。
    popen_kwargs: dict[str, object] = {
        "cwd": str(PROJECT_ROOT),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
        "close_fds": os.name != "nt",
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    subprocess.Popen(
        [str(project_pythonw), str(Path(__file__).resolve())],
        **popen_kwargs,
    )
    os._exit(0)


def _server_is_up(timeout: float = 0.3) -> bool:
    try:
        with urlopen(HOME_URL, timeout=timeout):
            return True
    except (OSError, URLError):
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((HOST, PORT)) == 0


def _open_browser_when_ready() -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        if _server_is_up():
            webbrowser.open(HOME_URL)
            return
        time.sleep(0.2)


def main(*, open_browser: bool = True) -> None:
    _ensure_stdio()
    os.chdir(PROJECT_ROOT)

    if open_browser:
        threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    if _server_is_up():
        return

    import uvicorn

    uvicorn.run(
        "wechat_article_reader.presentation.web.app:create_app",
        factory=True,
        host=HOST,
        port=PORT,
        log_level="info",
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    _ensure_stdio()
    try:
        os.chdir(PROJECT_ROOT)
        _restart_in_project_venv()
        main()
    except SystemExit as exc:
        if exc.code not in (0, None):
            _write_error(exc)
            _notify_failure(f"Web 启动失败，详见 error.log\n{exc}")
        raise
    except Exception as exc:
        _write_error(exc)
        _notify_failure(f"Web 启动失败，详见 error.log\n{exc}")
        raise SystemExit(1) from exc
