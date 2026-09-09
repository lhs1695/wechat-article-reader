from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def _load_launcher() -> dict:
    launcher = Path(__file__).parents[1] / "run_web.pyw"
    return runpy.run_path(str(launcher), run_name="run_web_test")


def test_restarts_with_project_pythonw(monkeypatch, tmp_path: Path):
    namespace = _load_launcher()
    project_pythonw = tmp_path / ".venv" / "Scripts" / "pythonw.exe"
    project_pythonw.parent.mkdir(parents=True)
    project_pythonw.touch()
    popen = Mock()
    exit_ = Mock(side_effect=SystemExit(0))
    launcher_globals = namespace["_restart_in_project_venv"].__globals__
    monkeypatch.setitem(launcher_globals, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "executable", "C:/Python312/pythonw.exe")
    monkeypatch.setattr(sys, "prefix", "C:/Python312")
    monkeypatch.setattr(launcher_globals["subprocess"], "Popen", popen)
    monkeypatch.setattr(launcher_globals["os"], "_exit", exit_)

    with pytest.raises(SystemExit):
        namespace["_restart_in_project_venv"]()

    popen.assert_called_once()
    args, kwargs = popen.call_args
    assert args[0] == [str(project_pythonw), str(Path(namespace["__file__"]).resolve())]
    assert kwargs["cwd"] == str(tmp_path)
    exit_.assert_called_once_with(0)


def test_skips_restart_when_sys_prefix_is_project_venv(monkeypatch, tmp_path: Path):
    namespace = _load_launcher()
    project_pythonw = tmp_path / ".venv" / "Scripts" / "pythonw.exe"
    project_pythonw.parent.mkdir(parents=True)
    project_pythonw.touch()
    popen = Mock()
    launcher_globals = namespace["_restart_in_project_venv"].__globals__
    monkeypatch.setitem(launcher_globals, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "executable", "C:/Python312/pythonw.exe")
    monkeypatch.setattr(sys, "prefix", str(tmp_path / ".venv"))
    monkeypatch.setattr(launcher_globals["subprocess"], "Popen", popen)

    namespace["_restart_in_project_venv"]()

    popen.assert_not_called()


def test_main_configures_uvicorn_for_pythonw(monkeypatch):
    uvicorn_run = Mock()
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=uvicorn_run))
    namespace = _load_launcher()
    launcher_globals = namespace["main"].__globals__
    monkeypatch.setattr(launcher_globals["os"], "chdir", lambda _path: None)
    monkeypatch.setitem(launcher_globals, "_server_is_up", lambda timeout=0.3: False)

    namespace["main"](open_browser=False)

    uvicorn_run.assert_called_once_with(
        "wechat_article_reader.presentation.web.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
        log_level="info",
        log_config=None,
        access_log=False,
    )


def test_main_reuses_existing_listener(monkeypatch):
    uvicorn_run = Mock()
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=uvicorn_run))
    namespace = _load_launcher()
    launcher_globals = namespace["main"].__globals__
    monkeypatch.setattr(launcher_globals["os"], "chdir", lambda _path: None)
    monkeypatch.setitem(launcher_globals, "_server_is_up", lambda timeout=0.3: True)

    namespace["main"](open_browser=False)

    uvicorn_run.assert_not_called()
