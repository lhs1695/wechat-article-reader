from __future__ import annotations

from sqlalchemy import create_engine, text

from wechat_article_reader.domain.entities import Summary
from wechat_article_reader.domain.value_objects import ArticleContent
from wechat_article_reader.infrastructure.persistence import (
    Database,
    SqlAlchemyStorage,
    upgrade_database,
)


def test_upgraded_database_does_not_keep_fts(tmp_path, sample_article) -> None:
    db_path = tmp_path / "articles.db"
    upgrade_database(db_path)
    storage = SqlAlchemyStorage(Database(db_path))
    storage.save(sample_article)

    engine = create_engine(f"sqlite:///{db_path.resolve().as_posix()}")
    try:
        with engine.connect() as connection:
            names = {
                row[0]
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE name = 'article_fts'")
                )
            }
    finally:
        engine.dispose()

    assert names == set()
    assert storage.get(sample_article.id) is not None
    assert storage.delete(sample_article.id) is True


def test_refreshed_content_does_not_load_stale_summary(tmp_path, sample_article) -> None:
    db_path = tmp_path / "articles.db"
    upgrade_database(db_path)
    storage = SqlAlchemyStorage(Database(db_path))
    sample_article.attach_summary(Summary(overview="旧正文的摘要"))
    storage.save(sample_article)
    assert storage.get(sample_article.id).summary is not None

    sample_article.update_content(ArticleContent.from_text("刷新后的不同正文"))
    storage.save(sample_article)
    refreshed = storage.get(sample_article.id)
    assert refreshed is not None
    assert refreshed.summary is None
