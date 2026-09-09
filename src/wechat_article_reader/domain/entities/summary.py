"""摘要领域值对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ...shared.utils import utc_now


@dataclass(frozen=True)
class Summary:
    """单次 DeepSeek 对完整文章生成的阅读摘要。"""

    overview: str
    key_points: tuple[str, ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)
    one_sentence: str = ""
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "key_points", tuple(str(point) for point in self.key_points))
        object.__setattr__(self, "tags", tuple(tag.strip() for tag in self.tags if tag.strip()))
