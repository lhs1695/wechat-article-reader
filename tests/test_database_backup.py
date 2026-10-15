"""SQLite online backup and restore verification tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from wechat_article_reader.infrastructure.persistence import (
    Database,
    backup_database,
    current_revision,
    upgrade_database,
    verify_database_backup,
)
from wechat_article_reader.infrastructure.persistence.storage import SqlAlchemyStorage


def test_backup_and_restore_verification_preserve_data_and_schema(
    tmp_path: Path, sample_article
) -> None:
    database_path = tmp_path / "source.sqlite3"
    backup_path = tmp_path / "backups" / "article-backup.sqlite3"
    upgrade_database(database_path)
    database = Database(database_path)
    try:
        source_storage = SqlAlchemyStorage(database)
        source_storage.save(sample_article)
        article_id = sample_article.id
        result_path = backup_database(database, backup_path)
    finally:
        database.dispose()

    assert result_path == backup_path.resolve()
    assert current_revision(database_path) == current_revision(backup_path)
    assert verify_database_backup(backup_path) == {
        "integrity": "ok",
        "schema_revision": current_revision(database_path),
    }
    restored_database = Database(backup_path)
    try:
        restored_article = SqlAlchemyStorage(restored_database).get(article_id)
    finally:
        restored_database.dispose()
    assert restored_article is not None
    assert restored_article.title == sample_article.title
    assert restored_article.content_text == sample_article.content_text


def test_backup_refuses_to_overwrite_existing_file(tmp_path: Path) -> None:
    database_path = tmp_path / "source.sqlite3"
    backup_path = tmp_path / "existing.sqlite3"
    upgrade_database(database_path)
    backup_path.write_bytes(b"keep this user file")
    database = Database(database_path)
    try:
        with pytest.raises(FileExistsError):
            backup_database(database, backup_path)
    finally:
        database.dispose()
    assert backup_path.read_bytes() == b"keep this user file"


def test_backup_rejects_source_as_destination(tmp_path: Path) -> None:
    database_path = tmp_path / "source.sqlite3"
    upgrade_database(database_path)
    database = Database(database_path)
    try:
        with pytest.raises(ValueError, match="不能与源数据库相同"):
            backup_database(database, database_path)
    finally:
        database.dispose()


def test_backup_verification_rejects_corrupt_database(tmp_path: Path) -> None:
    backup_path = tmp_path / "corrupt.sqlite3"
    backup_path.write_bytes(b"not sqlite")
    with pytest.raises(sqlite3.DatabaseError):
        verify_database_backup(backup_path)
