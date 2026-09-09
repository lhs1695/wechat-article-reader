"""HTML导出器

通过 MarkdownExporter 生成 MD，再渲染为 HTML，
保持与 Markdown 导出完全一致的内容。
"""

from html import escape
from pathlib import Path

from loguru import logger

from ....domain.entities import Article
from ....shared.exceptions import ExporterError
from ....shared.utils.html_safety import sanitize_html
from .base import BaseExporter
from .markdown import MarkdownExporter


class HtmlExporter(BaseExporter):
    """
    HTML导出器

    复用 MarkdownExporter 生成 Markdown 内容，
    再用 markdown_it 渲染为独立 HTML 文件。
    """

    def __init__(self, output_dir: str = "./output"):
        self._output_dir = Path(output_dir)

    @property
    def name(self) -> str:
        return "html"

    @property
    def target(self) -> str:
        return "html"

    def is_available(self) -> bool:
        return True

    def export(
        self,
        article: Article,
        path: str | None = None,
        **options,
    ) -> str:
        """导出为HTML文件"""
        if path:
            output_path = Path(path)
            if output_path.is_dir():
                output_path = output_path / self._generate_filename(article)
        else:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            output_path = self._output_dir / self._generate_filename(article)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        html_content = self._generate_html(article, **options)

        try:
            self._atomic_write_text(output_path, html_content)
            logger.info(f"HTML导出成功: {output_path}")
            return str(output_path)
        except Exception as e:
            raise ExporterError(f"HTML导出失败: {e}") from e

    def _generate_filename(self, article: Article) -> str:
        return self._default_filename(article, ".html")

    @staticmethod
    def _strip_frontmatter(md: str) -> str:
        """去除 YAML frontmatter（---...---），保留正文"""
        if md.startswith("---"):
            end = md.find("---", 3)
            if end != -1:
                return md[end + 3 :].lstrip()
        return md

    def _generate_html(self, article: Article, **options) -> str:
        """先生成 Markdown，再渲染为 HTML"""
        # 委托 MarkdownExporter 生成标准 MD 内容
        md_exporter = MarkdownExporter(output_dir=str(self._output_dir))
        html_options = dict(options)
        html_options.setdefault("include_images", True)
        md_content = md_exporter._generate_markdown(article, **html_options)

        # 去除 YAML frontmatter（markdown_it 不识别 frontmatter）
        md_body = self._strip_frontmatter(md_content)

        # 渲染 MD → HTML
        try:
            from markdown_it import MarkdownIt

            md_parser = MarkdownIt("commonmark", {"html": False})
            body_html = sanitize_html(md_parser.render(md_body))
        except ImportError:
            body_html = f"<pre>{escape(md_content)}</pre>"

        title = escape(article.title, quote=True)

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            line-height: 1.8;
            color: #333;
            background: #f5f5f5;
            padding: 20px;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
            background: white;
            padding: 40px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        .container img {{ max-width: 100%; height: auto; }}
        .container table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
        .container th, .container td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
        .container th {{ background: #f8f9fa; }}
        .container pre {{ background: #f6f8fa; padding: 16px; border-radius: 6px; overflow-x: auto; }}
        .container code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }}
        .container pre code {{ background: none; padding: 0; }}
        .container blockquote {{ border-left: 4px solid #07C160; padding-left: 16px; color: #666; margin: 1em 0; }}
        hr {{ border: none; border-top: 1px solid #eee; margin: 24px 0; }}
        a {{ color: #07C160; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <div class="container">
        {body_html}
    </div>
</body>
</html>"""
