"""Port for producing bounded, safe text input for article summarizers."""

from __future__ import annotations

from typing import Protocol

from ....domain.entities import Article


class SummarySourceRendererPort(Protocol):
    """Render an article into the text supplied to a summarizer."""

    def render(self, article: Article) -> str: ...
