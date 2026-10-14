"""建立首版 SQLite Schema 与 FTS5 索引。"""

import sqlalchemy as sa
from alembic import op

revision = "20260731_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Keep historical migrations independent of the current ORM model.
    existing = set(sa.inspect(bind).get_table_names())
    if "articles" not in existing:
        op.create_table(
            "articles",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("url", sa.Text(), nullable=False),
            sa.Column("normalized_url", sa.Text(), nullable=False, unique=True),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("author", sa.Text()),
            sa.Column("account_name", sa.Text()),
            sa.Column("publish_time", sa.DateTime()),
            sa.Column("content_html", sa.Text(), nullable=False),
            sa.Column("content_text", sa.Text(), nullable=False),
            sa.Column("images_json", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("source_json", sa.Text()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_articles_updated_at", "articles", ["updated_at"])
        op.create_index("ix_articles_content_hash", "articles", ["content_hash"])
    if "summaries" not in existing:
        op.create_table(
            "summaries",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "article_id",
                sa.String(36),
                sa.ForeignKey("articles.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("overview", sa.Text(), nullable=False),
            sa.Column("key_points_json", sa.Text(), nullable=False),
            sa.Column("tags_json", sa.Text(), nullable=False),
            sa.Column("one_sentence", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("article_id", "content_hash", name="uq_summary_generation"),
        )
        op.create_index("ix_summaries_article_created", "summaries", ["article_id", "created_at"])
    op.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS article_fts USING fts5("
        "article_id UNINDEXED, title, body, summary)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS article_fts")
    op.drop_table("summaries")
    op.drop_table("articles")
