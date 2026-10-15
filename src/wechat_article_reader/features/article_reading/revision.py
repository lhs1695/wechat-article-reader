"""Opaque versions for positions in a rendered article reading projection."""

from __future__ import annotations

from ...domain.entities import Article
from . import projection


def reading_projection_version() -> int:
    """Return the active projection format version."""
    return projection.PROJECTION_FORMAT_VERSION


def reading_revision(article_revision: str) -> str:
    """Bind an article snapshot to the projection rules used for its positions."""
    return f"rp{reading_projection_version()}:{article_revision}"


def article_reading_revision(article: Article) -> str:
    """Return the opaque reading version for an article entity."""
    return reading_revision(article.revision)
