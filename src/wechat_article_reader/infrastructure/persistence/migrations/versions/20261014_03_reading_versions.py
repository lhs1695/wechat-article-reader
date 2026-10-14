"""Exact content versions, summary provenance, and canonical URL identity."""

import uuid

import sqlalchemy as sa
from alembic import op

from wechat_article_reader.shared.utils.article_identity import (
    article_digest,
    normalize_article_url,
)

revision = "20261014_03"
down_revision = "20260827_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "articles", sa.Column("revision", sa.String(36), nullable=False, server_default="")
    )
    op.add_column(
        "summaries", sa.Column("input_hash", sa.String(64), nullable=False, server_default="")
    )
    op.add_column(
        "summaries", sa.Column("generation_json", sa.Text(), nullable=False, server_default="{}")
    )
    bind = op.get_bind()
    rows = (
        bind.execute(sa.text("SELECT * FROM articles ORDER BY updated_at DESC, id"))
        .mappings()
        .all()
    )
    # Preserve every old UUID, including duplicate URL entries. URL lookup uses
    # the newest entry; other snapshots remain readable by their existing UUID.
    for row in rows:
        bind.execute(
            sa.text("UPDATE articles SET normalized_url=:url WHERE id=:id"),
            {"url": "legacy:" + row["id"], "id": row["id"]},
        )
    seen: set[str] = set()
    for row in rows:
        url = normalize_article_url(row["url"])
        canonical = url if url not in seen else "legacy:" + row["id"]
        seen.add(url)
        digest = article_digest(
            row["content_html"],
            row["content_text"],
            row["title"],
            row["author"],
            row["account_name"],
        )
        bind.execute(
            sa.text(
                "UPDATE articles SET normalized_url=:url, content_hash=:digest, revision=:revision WHERE id=:id"
            ),
            {"url": canonical, "digest": digest, "revision": str(uuid.uuid4()), "id": row["id"]},
        )
        # Old summaries were fingerprinted with lossy text. Retain them as
        # historical records, but do not claim they match the exact new version.


def downgrade() -> None:
    with op.batch_alter_table("summaries") as batch:
        batch.drop_column("generation_json")
        batch.drop_column("input_hash")
    with op.batch_alter_table("articles") as batch:
        batch.drop_column("revision")
