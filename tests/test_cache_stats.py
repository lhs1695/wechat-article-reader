from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from click.testing import CliRunner

from wechat_article_reader.infrastructure.persistence import (
    Database,
    SqlAlchemyStorage,
    upgrade_database,
)
from wechat_article_reader.infrastructure.persistence.database import default_database_path
from wechat_article_reader.infrastructure.persistence.storage import DatabaseStats
from wechat_article_reader.presentation.cli.app import cli


def test_default_database_path_keeps_legacy_file(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "wechat_summarizer.db").write_bytes(b"legacy")
    monkeypatch.setattr(
        "wechat_article_reader.infrastructure.persistence.database.get_runtime_dir",
        lambda: runtime,
    )

    assert default_database_path() == runtime / "wechat_summarizer.db"


def test_default_database_path_prefers_new_file(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "wechat_summarizer.db").write_bytes(b"legacy")
    (runtime / "wechat_article_reader.db").write_bytes(b"current")
    monkeypatch.setattr(
        "wechat_article_reader.infrastructure.persistence.database.get_runtime_dir",
        lambda: runtime,
    )

    assert default_database_path() == runtime / "wechat_article_reader.db"


def test_get_stats_reports_counts_and_created_dates(tmp_path, sample_article) -> None:
    created = datetime(2026, 8, 25, 9, 3, 50)
    article = replace(sample_article, created_at=created, updated_at=created)

    db_path = tmp_path / "articles.db"
    upgrade_database(db_path)
    storage = SqlAlchemyStorage(Database(db_path))
    storage.save(article)

    stats = storage.get_stats()

    assert stats.total_entries == 1
    assert stats.summary_entries == 0
    assert stats.created_at_min.date() == created.date()
    assert stats.created_at_max.date() == created.date()
    assert stats.total_size_bytes > 0


def test_cache_stats_cli_prints_sqlite_counts(monkeypatch) -> None:
    class _Container:
        storage = type(
            "_Storage",
            (),
            {
                "get_stats": lambda self: DatabaseStats(
                    total_entries=11,
                    total_size_bytes=2048,
                    summary_entries=3,
                    created_at_min=datetime(2026, 8, 25, 9, 3, 50),
                    created_at_max=datetime(2026, 9, 7, 8, 38, 11),
                )
            },
        )()

    monkeypatch.setattr(
        "wechat_article_reader.presentation.cli.app.get_container",
        lambda: _Container(),
    )

    result = CliRunner().invoke(cli, ["cache-stats"])

    assert result.exit_code == 0
    assert "11" in result.output
    assert "3" in result.output
    assert "2026-08-25" in result.output
    assert "2026-09-07" in result.output
