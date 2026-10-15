"""Agent-friendly cached Markdown reading feature."""

from .dto import (
    ArticleExportResult,
    ArticleReadingMetadata,
    ArticleReadingProjection,
    ArticleReadPage,
    IngestResult,
    ReadingBlock,
    ReadingLink,
    ReadingSection,
)
from .projection import ArticleReadingProjector, content_digest
from .renderer import MarkdownReadingRenderer
from .revision import article_reading_revision, reading_projection_version, reading_revision
from .service import (
    ArticleNotFoundError,
    ArticlePageSizeError,
    ArticleReadingError,
    ArticleReadingService,
    ArticleStorageError,
    ReadingRevisionChangedError,
)
from .summary_renderer import SummarySourceRenderer

__all__ = [
    "ArticleExportResult",
    "ArticleNotFoundError",
    "ArticlePageSizeError",
    "ArticleReadPage",
    "ArticleReadingError",
    "ArticleReadingMetadata",
    "ArticleReadingProjection",
    "ArticleReadingProjector",
    "ArticleReadingService",
    "ArticleStorageError",
    "IngestResult",
    "MarkdownReadingRenderer",
    "ReadingBlock",
    "ReadingLink",
    "ReadingRevisionChangedError",
    "ReadingSection",
    "SummarySourceRenderer",
    "article_reading_revision",
    "content_digest",
    "reading_projection_version",
    "reading_revision",
]
