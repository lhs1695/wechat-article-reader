"""建立首版 SQLite Schema 与 FTS5 索引。"""

from alembic import op

from wechat_article_reader.infrastructure.persistence.models import Base

revision = "20260731_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    op.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS article_fts USING fts5("
        "article_id UNINDEXED, title, body, summary)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS article_fts")
    Base.metadata.drop_all(bind=op.get_bind())
