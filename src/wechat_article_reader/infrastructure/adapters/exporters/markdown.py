"""Markdown导出器"""

from pathlib import Path

from loguru import logger

from ....domain.entities import Article
from ....features.article_reading import ArticleReadingProjector, MarkdownReadingRenderer
from ....shared.exceptions import ExporterError
from .base import BaseExporter


def _escape_yaml_value(value: str) -> str:
    """转义 YAML 值中的特殊字符

    处理的字符：
    - 双引号 -> 转义
    - 换行符 -> 空格
    - 反斜杠 -> 双反斜杠
    """
    if not value:
        return ""
    # 先处理反斜杠，再处理其他
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    value = value.replace("\n", " ")
    value = value.replace("\r", " ")
    return value


class MarkdownExporter(BaseExporter):
    """
    Markdown导出器

    将文章导出为Markdown格式，适合在笔记软件中使用。
    """

    def __init__(self, output_dir: str = "./output"):
        self._output_dir = Path(output_dir)

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
        md_content = self._generate_markdown(article, **options)

        # 写入文件
        try:
            self._atomic_write_text(output_path, md_content)
            logger.info(f"Markdown导出成功: {output_path}")
            return str(output_path)
        except Exception as e:
            raise ExporterError(f"Markdown导出失败: {e}") from e

    def _generate_filename(self, article: Article) -> str:
        """生成文件名"""
        return self._default_filename(article, ".md")

    def _generate_markdown(self, article: Article, **options) -> str:
        """生成Markdown内容"""
        include_summary = options.get("include_summary", True)
        include_body = options.get("include_body", True)
        include_frontmatter = options.get("include_frontmatter", True)
        include_images = options.get("include_images", False)

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
            frontmatter_lines.append(f'url: "{article.url!s}"')
            frontmatter_lines.append(f"word_count: {article.word_count}")

            if article.summary and article.summary.tags:
                # 转义每个标签
                tags = ", ".join(f'"{_escape_yaml_value(t)}"' for t in article.summary.tags)
                frontmatter_lines.append(f"tags: [{tags}]")

            frontmatter_lines.append("---")
            frontmatter_lines.append("")
            parts.append("\n".join(frontmatter_lines))

        # 标题
        parts.append(f"# {article.title}\n")

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
            projection = ArticleReadingProjector().project(article)
            parts.append(
                MarkdownReadingRenderer().render(projection, include_images=include_images)
            )

        parts.append("")
        parts.append("---")
        parts.append("")
        parts.append(f"[原文链接]({article.url})")

        return "\n".join(parts)
