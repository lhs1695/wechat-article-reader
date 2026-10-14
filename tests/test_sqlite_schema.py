from __future__ import annotations

from uuid import UUID, uuid4

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


def test_legacy_migration_preserves_ids_and_canonicalizes_query_order(tmp_path):
    path = tmp_path / "legacy.db"
    upgrade_database(path, revision="20260827_02")
    old_id, new_id = str(uuid4()), str(uuid4())
    engine = create_engine(f"sqlite:///{path.resolve().as_posix()}")
    try:
        with engine.begin() as connection:
            for identifier, url, date in [
                (old_id, "https://mp.weixin.qq.com/s?mid=1&__biz=2", "2026-01-01"),
                (new_id, "https://mp.weixin.qq.com/s?__biz=2&mid=1", "2026-02-01"),
            ]:
                connection.execute(
                    text(
                        "INSERT INTO articles (id,url,normalized_url,title,content_html,content_text,images_json,content_hash,created_at,updated_at) VALUES (:id,:url,:url,'标题','<p>内容</p>','内容','[]','old',:date,:date)"
                    ),
                    {"id": identifier, "url": url, "date": date},
                )
            connection.execute(
                text(
                    "INSERT INTO summaries (id,article_id,content_hash,overview,key_points_json,tags_json,one_sentence,created_at) VALUES (:id,:article,'old','旧摘要','[]','[]','','2026-01-01')"
                ),
                {"id": str(uuid4()), "article": old_id},
            )
    finally:
        engine.dispose()
    upgrade_database(path)
    upgrade_database(path)
    database = Database(path)
    storage = SqlAlchemyStorage(database)
    try:
        assert storage.get(UUID(old_id)).summary is None
        assert storage.get(UUID(new_id)).revision
        assert str(storage.get_by_url("https://mp.weixin.qq.com/s?mid=1&__biz=2").id) == new_id
        with database.engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM summaries")) == 1
        # The preserved legacy UUID can still be updated independently.
        old = storage.get(UUID(old_id))
        old.update_content(ArticleContent.from_text("更新历史快照"))
        storage.save(old)
        assert storage.get(UUID(old_id)).content_text == "更新历史快照"
        assert str(storage.get_by_url("https://mp.weixin.qq.com/s?mid=1&__biz=2").id) == new_id
    finally:
        database.dispose()
