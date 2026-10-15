"""Stable per-revision image references and portable Markdown projections."""

from collections.abc import Iterable
from dataclasses import dataclass, replace

from bs4 import BeautifulSoup

from .dto import ArticleReadingProjection, ReadingBlock
from .projection import _safe_image_url


@dataclass(frozen=True)
class ArticleImage:
    image_id: str
    url: str
    alt: str
    caption: str
    block_cursor: int


def article_images(projection: ArticleReadingProjection) -> tuple[ArticleImage, ...]:
    images: list[ArticleImage] = []

    def append(url: str, alt: str, caption: str, cursor: int) -> None:
        images.append(ArticleImage(f"image-{len(images) + 1:03d}", url, alt, caption, cursor))

    def walk(blocks: Iterable[ReadingBlock], parent: int | None = None) -> None:
        for block in blocks:
            cursor = block.index if parent is None else parent
            if block.type == "image" and block.url:
                append(block.url, block.alt or "", block.caption or "", cursor)
            if (
                block.type == "table"
                and block.markdown
                and block.markdown.lstrip().startswith("<table")
            ):
                for node in BeautifulSoup(block.markdown, "html.parser").find_all("img"):
                    url = _safe_image_url(node.get("src"))
                    if url:
                        append(url, str(node.get("alt") or ""), "", cursor)
            walk(block.children, cursor)

    walk(projection.blocks)
    return tuple(images)


def local_image_projection(
    projection: ArticleReadingProjection, paths: dict[str, str]
) -> ArticleReadingProjection:
    def walk(block: ReadingBlock) -> ReadingBlock:
        markdown = block.markdown
        if block.type == "table" and markdown and markdown.lstrip().startswith("<table"):
            soup = BeautifulSoup(markdown, "html.parser")
            for node in soup.find_all("img"):
                url = str(node.get("src") or "")
                if url in paths:
                    node["src"] = paths[url]
            markdown = str(soup)
        return replace(
            block,
            url=paths.get(block.url, block.url) if block.url else None,
            markdown=markdown,
            children=tuple(walk(child) for child in block.children),
        )

    return replace(projection, blocks=tuple(walk(block) for block in projection.blocks))
