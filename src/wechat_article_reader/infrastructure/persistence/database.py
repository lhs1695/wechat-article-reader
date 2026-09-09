"""SQLAlchemy Engine 与 Session 工厂。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ..config.paths import get_runtime_dir

DEFAULT_DATABASE_NAME = "wechat_article_reader.db"
LEGACY_DATABASE_NAME = "wechat_summarizer.db"


def default_database_path() -> Path:
    runtime = get_runtime_dir()
    current = runtime / DEFAULT_DATABASE_NAME
    if current.exists():
        return current
    legacy = runtime / LEGACY_DATABASE_NAME
    if legacy.exists():
        return legacy
    return current


def database_url(path: str | Path | None = None) -> str:
    target = Path(path) if path is not None else default_database_path()
    return f"sqlite:///{target.resolve().as_posix()}"


class Database:
    """持有进程级 Engine；Session 仍按事务创建。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_sqlite_engine(self.path)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
        )

    def dispose(self) -> None:
        self.engine.dispose()


def create_sqlite_engine(path: str | Path) -> Engine:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        database_url(target),
        connect_args={"check_same_thread": False, "timeout": 5.0},
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()

    return engine
