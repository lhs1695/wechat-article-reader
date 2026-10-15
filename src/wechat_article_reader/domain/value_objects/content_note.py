"""Evidence-based notes about known limits in captured article content."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

ContentNoteScope = Literal["article", "page", "image", "summary"]


@dataclass(frozen=True)
class ContentNote:
    code: str
    message: str
    scope: ContentNoteScope = "article"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    def __str__(self) -> str:
        return self.message
