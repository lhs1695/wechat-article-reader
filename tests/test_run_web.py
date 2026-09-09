from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


def _load_launcher() -> dict:
    launcher = Path(__file__).parents[1] / "run_web.pyw"
    return runpy.run_path(str(launcher), run_name="run_web_test")


def test_restarts_with_project_pythonw(monkeypatch, tmp_path: Path):
    namespace = _load_launcher()
    project_pythonw = tmp_path / ".venv" / "Scripts" / "pythonw.exe"
    project_pythonw.parent.mkdir(parents=True)
    project_pythonw.touch()
    execv = Mock()
    launcher_globals = namespace["_restart_in_project_venv"].__globals__
    monkeypatch.setitem(launcher_globals, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "executable", "C:/Python312/pythonw.exe")
    monkeypatch.setattr(launcher_globals["os"], "execv", execv)

    namespace["_restart_in_project_venv"]()

    execv.assert_called_once_with(
        str(project_pythonw),
        [str(project_pythonw), str(Path(namespace["__file__"]).resolve())],
    )


def test_main_configures_uvicorn_for_pythonw(monkeypatch):
    uvicorn_run = Mock()
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=uvicorn_run))
    namespace = _load_launcher()

    namespace["main"]()

    uvicorn_run.assert_called_once_with(
        "wechat_article_reader.presentation.web.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
        log_level="info",
        log_config=None,
        access_log=False,
    )
