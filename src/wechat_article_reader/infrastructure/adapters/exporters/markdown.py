"""Markdown导出器"""

import json
from hashlib import sha256
from pathlib import Path
from threading import Event

from loguru import logger

from ....application.ports.outbound.image_asset_port import ImageAssetPort
from ....domain.entities import Article
from ....features.article_reading import ArticleReadingProjector, MarkdownReadingRenderer
from ....features.article_reading.dto import ArticleReadingProjection
from ....features.article_reading.images import article_images, local_image_projection
from ....features.article_reading.projection import escape_markdown_text, markdown_destination
from ....shared.exceptions import ExporterError, OperationCancelledError
from ..image_assets import atomic_write_bytes
from .base import BaseExporter


def _escape_yaml_value(value: str) -> str:
    """Encode a YAML double-quoted scalar using JSON-compatible escapes."""
    return (
        json.dumps(value, ensure_ascii=False)[1:-1]
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


class MarkdownExporter(BaseExporter):
    """
    Markdown导出器

    将文章导出为Markdown格式，适合在笔记软件中使用。
    """

    def __init__(self, output_dir: str = "./output", image_assets: ImageAssetPort | None = None):
        self._output_dir = Path(output_dir)
        self._image_assets = image_assets

    @property
    def name(self) -> str:
        return "markdown"

    @property
    def target(self) -> str:
        return "markdown"

    def is_available(self) -> bool:
        return True

    def export(
        self,
        article: Article,
        path: str | None = None,
        **options,
    ) -> str:
        """导出为Markdown文件"""
        if not self.is_available():
            raise ExporterError("markdownify未安装，请运行: pip install markdownify")

        # 确定输出路径
        if path:
            output_path = Path(path)
            if output_path.is_dir():
                output_path = output_path / self._generate_filename(article)
        else:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            output_path = self._output_dir / self._generate_filename(article)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 生成Markdown内容
        download_images = options.get("download_images", False)
        if type(download_images) is not bool:
            raise ValueError("download_images must be a boolean")
        if download_images and not options.get("include_body", True):
            raise ValueError("下载图片需要 include_body=true")
        if download_images:
            projection = ArticleReadingProjector().project(article)
            projection, manifest, failures = self._download_images(
                article, projection, output_path, options.get("cancel_event")
            )
            options["projection"] = projection
            options["image_failures"] = failures
            # Publish the manifest before Markdown so every local reference exists.
            self._atomic_write_text(
                output_path.with_suffix(".assets") / "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
                cancel_event=options.get("cancel_event"),
            )
        md_content = self._generate_markdown(article, **options)

        # 写入文件
        try:
            self._atomic_write_text(
                output_path, md_content, cancel_event=options.get("cancel_event")
            )
            logger.info(f"Markdown导出成功: {output_path}")
            return str(output_path)
        except OperationCancelledError:
            raise
        except Exception as e:
            raise ExporterError(f"Markdown导出失败: {e}") from e

    def _generate_filename(self, article: Article) -> str:
        """生成文件名"""
        return self._default_filename(article, ".md")

    def _download_images(
        self,
        article: Article,
        projection: ArticleReadingProjection,
        output_path: Path,
        cancel_event: Event | None,
    ) -> tuple[ArticleReadingProjection, dict[str, object], list[str]]:
        from dataclasses import asdict
        from urllib.parse import quote

        assets_dir = output_path.with_suffix(".assets")
        revision_dir = (
            assets_dir / sha256(f"{article.id}:{article.revision}".encode()).hexdigest()[:16]
        )
        if not revision_dir.resolve().is_relative_to(output_path.parent.resolve()):
            raise ValueError("图片目录必须位于导出目录内")
        revision_dir.mkdir(parents=True, exist_ok=True)
        paths, entries, failures = {}, [], []
        total_bytes = 0
        downloaded: dict[str, tuple[str, str, int]] = {}
        failed_urls: dict[str, str] = {}
        for image in article_images(projection):
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelledError()
            entry = asdict(image)
            try:
                if self._image_assets is None:
                    raise ValueError("图片下载器未配置")
                if image.url in failed_urls:
                    raise ValueError(failed_urls[image.url])
                if image.url not in downloaded:
                    if len(downloaded) + len(failed_urls) >= 100 or total_bytes >= 50 * 1024 * 1024:
                        raise ValueError("超过单篇 100 张或 50 MiB 的下载预算")
                    asset = self._image_assets.load(
                        image.url, download=True, cancel_event=cancel_event
                    )
                    if total_bytes + len(asset.data) > 50 * 1024 * 1024:
                        raise ValueError("超过单篇图片下载预算")
                    name = sha256(asset.data).hexdigest() + asset.extension
                    target = revision_dir / name
                    atomic_write_bytes(target, asset.data, cancel_event)
                    relative = target.relative_to(output_path.parent).as_posix()
                    downloaded[image.url] = (relative, asset.mime_type, asset.frame_count)
                    total_bytes += len(asset.data)
                local_path, mime_type, frame_count = downloaded[image.url]
                paths[image.url] = quote(local_path, safe="/")
                entry.update(
                    status="downloaded",
                    local_path=local_path,
                    mime_type=mime_type,
                    frame_count=frame_count,
                )
            except OperationCancelledError:
                raise
            except Exception as exc:
                failures.append(image.image_id)
                failed_urls[image.url] = (
                    str(exc) if isinstance(exc, ValueError) else "图片下载或校验失败；保留原始链接"
                )
                entry.update(
                    status="failed",
                    error=failed_urls[image.url],
                )
            entries.append(entry)
        manifest: dict[str, object] = {
            "article_id": str(article.id),
            "revision": article.revision,
            "source_trust": "untrusted_web_content",
            "images": entries,
            "image_content_read": False,
        }
        return local_image_projection(projection, paths), manifest, failures

    def _generate_markdown(self, article: Article, **options) -> str:
        """生成Markdown内容"""
        include_summary = options.get("include_summary", True)
        include_body = options.get("include_body", True)
        include_frontmatter = options.get("include_frontmatter", True)
        include_images = options.get("include_images", False)
        projection = options.get("projection") or (
            ArticleReadingProjector().project(article) if include_body else None
        )
        body_blocks = list(projection.blocks) if projection else []
        if (
            body_blocks
            and body_blocks[0].type == "heading"
            and body_blocks[0].text.strip() == article.title.strip()
        ):
            body_blocks.pop(0)

        parts = []

        # YAML Front Matter（转义所有用户输入）
        if include_frontmatter:
            frontmatter_lines = [
                "---",
                f'title: "{_escape_yaml_value(article.title)}"',
            ]
            if article.account_name:
                frontmatter_lines.append(f'source: "{_escape_yaml_value(article.account_name)}"')
            if article.author:
                frontmatter_lines.append(f'author: "{_escape_yaml_value(article.author)}"')
            if article.publish_time:
                frontmatter_lines.append(f"date: {article.publish_time_str}")
            frontmatter_lines.append(f'url: "{_escape_yaml_value(str(article.url))}"')
            frontmatter_lines.append(f"word_count: {article.word_count}")

            if article.summary and article.summary.tags:
                # 转义每个标签
                tags = ", ".join(f'"{_escape_yaml_value(t)}"' for t in article.summary.tags)
                frontmatter_lines.append(f"tags: [{tags}]")

            frontmatter_lines.append("---")
            frontmatter_lines.append("")
            parts.append("\n".join(frontmatter_lines))

        # 标题
        parts.append(f"# {escape_markdown_text(article.title)}\n")
        if options.get("download_images"):
            parts.append(
                "图片下载清单：本 Markdown 同名 `.assets/manifest.json`。下载不代表已理解图片；需要图文阅读时请逐张调用图片查看能力。\n"
            )
        if options.get("image_failures"):
            parts.append(
                "> 以下图片未成功下载，离线包不完整："
                + ", ".join(options["image_failures"])
                + "。请检查图片清单。\n"
            )

        # 摘要部分
        if include_summary and article.summary:
            parts.append("---")
            parts.append("")
            parts.append("## 文章摘要")
            parts.append("")
            parts.append(article.summary.overview)
            if article.summary.key_points:
                parts.append("")
                parts.append("### 关键要点")
                parts.extend(f"- {point}" for point in article.summary.key_points)
            if article.summary.one_sentence:
                parts.extend(["", "### 一句话总结", article.summary.one_sentence])
            if article.summary.tags:
                parts.extend(["", "### 标签", ", ".join(article.summary.tags)])
            parts.append("")

        if include_body:
            parts.append(
                MarkdownReadingRenderer().render(body_blocks, include_images=include_images)
            )

        parts.append("")
        parts.append("---")
        parts.append("")
        parts.append(f"[原文链接](<{markdown_destination(str(article.url))}>)")

        return "\n".join(parts)
