"""Alembic 的程序化入口。"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from .database import database_url, default_database_path


def _config(path: str | Path | None = None) -> Config:
    migrations_dir = Path(__file__).with_name("migrations")
    config = Config()
    config.set_main_option("script_location", str(migrations_dir))
    config.set_main_option("sqlalchemy.url", database_url(path).replace("%", "%%"))
    return config


def upgrade_database(path: str | Path | None = None, revision: str = "head") -> None:
    command.upgrade(_config(path), revision)


def downgrade_database(path: str | Path | None = None, revision: str = "-1") -> None:
    command.downgrade(_config(path), revision)


def current_revision(path: str | Path | None = None) -> str | None:
    target = Path(path) if path is not None else default_database_path()
    if not target.exists():
        return None
    engine = create_engine(database_url(target))
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()
