"""Drop unused FTS5 index after search tools were removed."""

from alembic import op

revision = "20260827_02"
down_revision = "20260731_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS article_fts")


def downgrade() -> None:
    op.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS article_fts USING fts5("
        "article_id UNINDEXED, title, body, summary)"
    )
