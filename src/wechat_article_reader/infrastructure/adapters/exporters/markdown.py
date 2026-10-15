"""Markdown导出器"""

import json
import os
import shutil
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event

from loguru import logger

from ....application.ports.outbound.image_asset_port import ImageAssetPort
from ....domain.entities import Article
from ....features.article_reading import ArticleReadingProjector, MarkdownReadingRenderer
from ....features.article_reading.dto import ArticleReadingProjection
from ....features.article_reading.images import article_images, local_image_projection
from ....features.article_reading.projection import (
    PROJECTION_FORMAT_VERSION,
    escape_markdown_text,
    markdown_destination,
)
from ....shared.exceptions import ExporterError, OperationCancelledError
from ..image_assets import atomic_write_bytes
from .base import BaseExporter
from .path_lock import export_path_lock


def _escape_yaml_value(value: str) -> str:
    """Encode a YAML double-quoted scalar using JSON-compatible escapes."""
    return (
        json.dumps(value, ensure_ascii=False)[1:-1]
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _export_snapshot_metadata(
    article: Article, options: dict[str, object], markdown_content: str
) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "article_id": str(article.id),
        "revision": article.revision,
        "url": str(article.url),
        "projection_version": PROJECTION_FORMAT_VERSION,
        "options": options,
        "markdown_sha256": sha256(markdown_content.encode("utf-8")).hexdigest(),
    }
    snapshot_id = sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    snapshot["snapshot_id"] = snapshot_id
    return snapshot


def _with_snapshot_marker(markdown_content: str, snapshot: dict[str, object]) -> str:
    marker = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{markdown_content}\n\n<!-- wechat-article-reader-export: {marker} -->\n"


def _stage_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
        return temporary
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


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
        with export_path_lock(output_path):
            return self._export_snapshot(article, output_path, options)

    def _export_snapshot(
        self, article: Article, output_path: Path, options: dict[str, object]
    ) -> str:
        snapshot_article = replace(article)
        download_images = options.get("download_images", False)
        if type(download_images) is not bool:
            raise ValueError("download_images must be a boolean")
        include_body = options.get("include_body", True)
        if type(include_body) is not bool:
            raise ValueError("include_body must be a boolean")
        include_images = options.get("include_images", False)
        if include_images is None:
            include_images = False
        if type(include_images) is not bool:
            raise ValueError("include_images must be a boolean")
        if download_images and not include_body:
            raise ValueError("下载图片需要 include_body=true")
        for key, default in (("include_summary", True), ("include_frontmatter", True)):
            if type(options.get(key, default)) is not bool:
                raise ValueError(f"{key} must be a boolean")
        include_summary = options.get("include_summary", True)
        include_frontmatter = options.get("include_frontmatter", True)
        cancel_event = options.get("cancel_event")
        if cancel_event is not None and not isinstance(cancel_event, Event):
            raise ValueError("cancel_event must be a threading.Event")

        snapshot_options = {
            "include_body": include_body,
            "include_images": include_images,
            "download_images": download_images,
            "include_summary": include_summary,
            "include_frontmatter": include_frontmatter,
        }
        projection = ArticleReadingProjector().project(snapshot_article) if include_body else None
        manifest: dict[str, object] | None = None
        failures: list[str] = []
        if download_images:
            assert projection is not None
            projection, manifest, failures = self._download_images(
                snapshot_article, projection, output_path, cancel_event
            )
        md_options = dict(options)
        md_options.update(
            include_body=include_body,
            include_images=include_images,
            projection=projection,
            image_failures=failures,
        )
        md_content = self._generate_markdown(snapshot_article, **md_options)

        try:
            if download_images:
                assert manifest is not None
                snapshot = _export_snapshot_metadata(snapshot_article, snapshot_options, md_content)
                manifest["snapshot"] = snapshot
                manifest_content = json.dumps(manifest, ensure_ascii=False, indent=2)
                markdown_content = _with_snapshot_marker(md_content, snapshot)
                self._publish_markdown_manifest(
                    output_path,
                    markdown_content,
                    output_path.with_suffix(".assets") / "manifest.json",
                    manifest_content,
                    cancel_event,
                )
            else:
                self._atomic_write_text(
                    output_path,
                    md_content,
                    cancel_event=cancel_event,
                )
            logger.info(f"Markdown导出成功: {output_path}")
            return str(output_path)
        except OperationCancelledError:
            raise
        except Exception as e:
            raise ExporterError(f"Markdown导出失败: {e}") from e

    @staticmethod
    def _publish_markdown_manifest(
        markdown_path: Path,
        markdown_content: str,
        manifest_path: Path,
        manifest_content: str,
        cancel_event: Event | None,
    ) -> None:
        """Stage both files, then publish with rollback if Markdown replacement fails."""
        staged_paths: list[Path] = []
        manifest_backup: Path | None = None
        manifest_published = False
        preserve_backup = False
        try:
            markdown_stage = _stage_text(markdown_path, markdown_content)
            staged_paths.append(markdown_stage)
            manifest_stage = _stage_text(manifest_path, manifest_content)
            staged_paths.append(manifest_stage)
            if manifest_path.is_file():
                with NamedTemporaryFile(
                    dir=manifest_path.parent,
                    prefix=f".{manifest_path.name}.",
                    suffix=".bak",
                    delete=False,
                ) as stream:
                    manifest_backup = Path(stream.name)
                shutil.copy2(manifest_path, manifest_backup)
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelledError()
            os.replace(manifest_stage, manifest_path)
            manifest_published = True
            os.replace(markdown_stage, markdown_path)
        except Exception as exc:
            if manifest_published:
                try:
                    if manifest_backup is None:
                        manifest_path.unlink(missing_ok=True)
                    else:
                        os.replace(manifest_backup, manifest_path)
                        manifest_backup = None
                except OSError as rollback_error:
                    preserve_backup = manifest_backup is not None
                    raise ExporterError(
                        f"Markdown 发布失败且 manifest 回滚失败: {rollback_error}; "
                        f"旧清单备份保留于 {manifest_backup}"
                    ) from exc
            raise
        finally:
            for staged_path in staged_paths:
                staged_path.unlink(missing_ok=True)
            if manifest_backup is not None and not preserve_backup:
                manifest_backup.unlink(missing_ok=True)

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
