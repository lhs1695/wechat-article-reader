from pathlib import Path

from wechat_article_reader.domain.entities import Article
from wechat_article_reader.domain.value_objects import ArticleContent, ArticleURL
from wechat_article_reader.infrastructure.adapters.exporters.html import HtmlExporter
from wechat_article_reader.infrastructure.adapters.exporters.markdown import MarkdownExporter


def test_html_exporter_writes_file(tmp_path: Path) -> None:
    article = Article(
        url=ArticleURL.from_string("https://mp.weixin.qq.com/s/xxx"),
        title='A/B:C*?"<>|',
        content=ArticleContent.from_text("hello"),
    )

    exporter = HtmlExporter(output_dir=str(tmp_path))
    out_path = Path(exporter.export(article))

    assert out_path.suffix == ".html"
    assert out_path.exists()


def test_html_exporter_escapes_title_and_raw_html(tmp_path: Path) -> None:
    article = Article(
        url=ArticleURL.from_string("https://mp.weixin.qq.com/s/xxx"),
        title='</title><script>alert("title")</script>',
        content=ArticleContent(
            html='<p>safe</p><svg onload="alert(1)"></svg><img src=x onerror="alert(2)">',
            text="safe",
        ),
    )

    output = Path(HtmlExporter(output_dir=str(tmp_path)).export(article)).read_text(
        encoding="utf-8"
    )

    assert "<script" not in output
    assert "onerror" not in output
    assert "onload" not in output
    assert "&lt;/title&gt;" in output


def test_exporters_create_parent_for_explicit_file_path(tmp_path: Path) -> None:
    article = Article(
        url=ArticleURL.from_string("https://mp.weixin.qq.com/s/xxx"),
        title="测试文章",
        content=ArticleContent.from_text("hello"),
    )

    markdown_path = tmp_path / "nested" / "article.md"
    html_path = tmp_path / "nested-html" / "article.html"

    assert Path(MarkdownExporter().export(article, path=str(markdown_path))) == markdown_path
    assert Path(HtmlExporter().export(article, path=str(html_path))) == html_path
    assert markdown_path.is_file()
    assert html_path.is_file()


def test_markdown_export_defaults_to_no_images_and_no_body_wrapper(tmp_path: Path) -> None:
    article = Article(
        url=ArticleURL.from_string("https://mp.weixin.qq.com/s/xxx"),
        title="导出样例",
        content=ArticleContent(
            html=(
                "<p>正文段</p>"
                "<figure><img src='https://mmbiz.qpic.cn/a.png' alt='图'>"
                "<figcaption>示意图</figcaption></figure>"
            ),
            text="正文段",
        ),
    )

    default_text = Path(MarkdownExporter(output_dir=str(tmp_path)).export(article)).read_text(
        encoding="utf-8"
    )
    with_images = Path(
        MarkdownExporter(output_dir=str(tmp_path)).export(
            article, path=str(tmp_path / "with-images.md"), include_images=True
        )
    ).read_text(encoding="utf-8")

    assert "## 原文内容" not in default_text
    assert "**字数**" not in default_text
    assert "正文段" in default_text
    assert "mmbiz.qpic.cn" not in default_text
    assert "示意图" in default_text
    assert "mmbiz.qpic.cn" in with_images
    first = Article(
        url=ArticleURL.from_string("https://mp.weixin.qq.com/s/first"),
        title="同名文章",
        content=ArticleContent.from_text("first"),
    )
    second = Article(
        url=ArticleURL.from_string("https://mp.weixin.qq.com/s/second"),
        title="同名文章",
        content=ArticleContent.from_text("second"),
    )
    exporter = MarkdownExporter(output_dir=str(tmp_path))

    first_path = Path(exporter.export(first))
    second_path = Path(exporter.export(second))

    assert first_path != second_path
    assert first_path.read_text(encoding="utf-8") != second_path.read_text(encoding="utf-8")
