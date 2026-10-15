"""Consistent SQLite backup and isolated restore verification."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from sqlalchemy import create_engine

from .database import Database, database_url
from .migration import current_revision


def _integrity_check(path: Path) -> None:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise ValueError(f"SQLite integrity check failed: {result!r}")
    finally:
        connection.close()


def backup_database(database: Database, destination: str | Path) -> Path:
    """Create an online SQLite backup without replacing an existing destination."""
    source = database.path.resolve()
    target = Path(destination).expanduser().resolve()
    if source == target:
        raise ValueError("备份目标不能与源数据库相同")
    if not source.is_file():
        raise FileNotFoundError(f"数据库不存在: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)

    # Reserve the requested path exclusively so concurrent backups never overwrite it.
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    temporary: Path | None = None
    source_connection = None
    destination_connection: sqlite3.Connection | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
        source_connection = database.engine.raw_connection()
        destination_connection = sqlite3.connect(temporary)
        source_driver = source_connection.driver_connection
        if source_driver is None:
            raise RuntimeError("无法获取 SQLite 源连接")
        source_driver.backup(destination_connection)
        destination_connection.close()
        destination_connection = None
        _integrity_check(temporary)
        os.replace(temporary, target)
        temporary = None
        return target
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def verify_database_backup(backup_path: str | Path) -> dict[str, str | None]:
    """Restore a backup into a temporary SQLite file and verify integrity/schema."""
    source = Path(backup_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"备份文件不存在: {source}")

    temporary_path: Path | None = None
    source_engine = create_engine(database_url(source))
    destination_connection: sqlite3.Connection | None = None
    source_connection = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as stream:
            temporary_path = Path(stream.name)
        source_connection = source_engine.raw_connection()
        destination_connection = sqlite3.connect(temporary_path)
        source_driver = source_connection.driver_connection
        if source_driver is None:
            raise RuntimeError("无法获取 SQLite 备份连接")
        source_driver.backup(destination_connection)
        destination_connection.close()
        destination_connection = None
        _integrity_check(temporary_path)
        revision = current_revision(temporary_path)
        if revision is None:
            raise ValueError("备份中没有可识别的 SQLite / Alembic schema")
        return {"integrity": "ok", "schema_revision": revision}
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()
        source_engine.dispose()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
