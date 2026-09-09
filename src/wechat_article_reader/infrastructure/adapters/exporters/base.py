"""导出器基类"""

import re
from abc import ABC, abstractmethod
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

from ....domain.entities import Article


class BaseExporter(ABC):
    @staticmethod
    def _default_filename(article: Article, suffix: str) -> str:
        """Keep same-title articles distinct without exposing arbitrary user paths."""
        safe_title = re.sub(r'[\\/*?:"<>|]', "", article.title).strip()[:40] or "untitled"
        source_hash = sha256(str(article.url).encode("utf-8")).hexdigest()[:8]
        return f"{safe_title}-{source_hash}{suffix}"

    @staticmethod
    def _atomic_write_text(output_path: Path, content: str) -> None:
        """Write in the target directory, then atomically replace the final file."""
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=output_path.parent,
                prefix=f".{output_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                temporary_path = Path(temporary.name)
            temporary_path.replace(output_path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink(missing_ok=True)

    """导出器抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """导出器名称"""
        pass

    @property
    @abstractmethod
    def target(self) -> str:
        """导出目标标识"""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """检查是否可用"""
        pass

    @abstractmethod
    def export(
        self,
        article: Article,
        path: str | None = None,
        **options,
    ) -> str:
        """导出文章"""
        pass
