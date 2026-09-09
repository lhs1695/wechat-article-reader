from __future__ import annotations

from pathlib import Path

from wechat_article_reader.infrastructure.config import paths


def test_runtime_subdirectories_use_configured_runtime_dir(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("WECHAT_ARTICLE_READER_RUNTIME_DIR", str(runtime_dir))

    assert paths.get_runtime_dir() == runtime_dir
    assert paths.get_cache_dir() == runtime_dir / "cache"
    assert paths.get_data_dir() == runtime_dir / "data"
    assert paths.get_log_dir() == runtime_dir / "logs"
    assert (runtime_dir / "cache").is_dir()
    assert (runtime_dir / "data").is_dir()
    assert (runtime_dir / "logs").is_dir()
