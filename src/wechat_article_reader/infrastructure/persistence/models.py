"""仅供基础设施层使用的 SQLAlchemy 映射。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ArticleModel(Base):
    __tablename__ = "articles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author: Mapped[str | None] = mapped_column(Text)
    account_name: Mapped[str | None] = mapped_column(Text)
    publish_time: Mapped[datetime | None]
    content_html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    images_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        Index("ix_articles_updated_at", "updated_at"),
        Index("ix_articles_content_hash", "content_hash"),
    )


class SummaryModel(Base):
    __tablename__ = "summaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    article_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("articles.id", ondelete="CASCADE"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    overview: Mapped[str] = mapped_column(Text, nullable=False)
    key_points_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    one_sentence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "article_id",
            "content_hash",
            name="uq_summary_generation",
        ),
        Index("ix_summaries_article_created", "article_id", "created_at"),
    )
