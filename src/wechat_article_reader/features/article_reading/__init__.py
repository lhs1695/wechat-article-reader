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
from .service import (
    ArticleNotFoundError,
    ArticlePageSizeError,
    ArticleReadingError,
    ArticleReadingService,
    ArticleStorageError,
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
    "ReadingSection",
    "SummarySourceRenderer",
    "content_digest",
]
