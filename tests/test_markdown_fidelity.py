"""Assert the semantics a reader sees, not just Markdown substring presence."""

from __future__ import annotations

import json

import pytest
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

from wechat_article_reader.domain.entities import Article
from wechat_article_reader.domain.value_objects import ArticleContent, ArticleURL
from wechat_article_reader.features.article_reading import (
    ArticleReadingProjector,
    MarkdownReadingRenderer,
)
from wechat_article_reader.infrastructure.adapters.exporters.markdown import MarkdownExporter
from wechat_article_reader.shared.utils.html_safety import sanitize_html


def _article(html: str, title: str = "测试") -> Article:
    return Article(
        url=ArticleURL.from_string('https://mp.weixin.qq.com/s/test?arg="quoted"'),
        title=title,
        content=ArticleContent.from_html(sanitize_html(html)),
    )


def _render(html: str):
    projection = ArticleReadingProjector().project(_article(html))
    md = MarkdownReadingRenderer().render(projection)
    rendered = MarkdownIt("commonmark", {"html": True}).enable("table").render(md)
    return projection, md, BeautifulSoup(rendered, "html.parser")


def test_python_code_preserves_indentation_blank_lines_and_language():
    source = 'def f():\n\tif True:\n\t\treturn "a  b"\n\n    return 0\n'
    _, _, dom = _render(f'<pre><code class="language-python">{source}</code></pre>')
    assert dom.code.get_text() == source
    assert dom.code["class"] == ["language-python"]


def test_highlighted_code_tokens_do_not_create_newlines():
    _, _, dom = _render(
        "<pre><code><span>foo</span><span>(</span><span>bar</span><span>)</span><br>    next()</code></pre>"
    )
    assert dom.code.get_text() == "foo(bar)\n    next()\n"


def test_long_code_is_one_complete_block_and_fence_cannot_close_it():
    source = "\n".join(f"    value_{index} = {index}" for index in range(100)) + "\n````\nafter"
    projection, _, dom = _render(f"<pre>{source}</pre>")
    assert len(projection.blocks) == 1
    assert len(dom.find_all("pre")) == 1
    assert dom.code.get_text() == source + "\n"


def test_long_table_retains_rows_and_links():
    html = (
        "<table><tr><th>Name</th><th>URL</th></tr>"
        + "".join(
            f'<tr><td>row{index}</td><td><a href="https://example.org/{index}">docs{index}</a></td></tr>'
            for index in range(60)
        )
        + "</table>"
    )
    projection, _, dom = _render(html)
    assert len(projection.blocks) == 1
    assert len(dom.select("tbody tr")) == 60
    assert len(dom.select("table a")) == 60
    assert len(projection.blocks[0].links) == 60


def test_table_pipe_and_linebreak_stay_in_their_cell():
    _, _, dom = _render(
        "<table><tr><th>A</th><th>B</th></tr><tr><td>x | y</td><td>one<br>two</td></tr></table>"
    )
    assert len(dom.select("tbody tr")) == 1
    assert dom.select("td")[0].get_text() == "x | y"
    assert dom.select("td")[1].br is not None


def test_complex_table_preserves_span_and_sanitizes_html():
    _, _, dom = _render(
        '<table onclick="evil()"><tr><th colspan="2">Group</th></tr><tr><td rowspan="2">A</td><td>B</td></tr><tr><td>C</td></tr></table>'
    )
    assert dom.th["colspan"] == "2"
    assert dom.td["rowspan"] == "2"
    assert not dom.table.has_attr("onclick")


def test_table_without_header_keeps_first_row_as_data():
    _, _, dom = _render("<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>")
    assert len(dom.select("tbody tr")) == 2
    assert dom.select("tbody td")[0].get_text() == "A"


def test_nested_ordered_list_keeps_start_number_and_paragraphs():
    _, _, dom = _render(
        '<ol start="3"><li>first<ul><li>child</li></ul><p>second paragraph</p></li><li>next</li></ol>'
    )
    assert dom.ol["start"] == "3"
    assert dom.select("ol > li > ul > li")[0].get_text() == "child"
    assert "second paragraph" in dom.select("ol > li")[0].get_text()


def test_quote_preserves_code_and_paragraphs():
    _, _, dom = _render("<blockquote><p>quoted</p><pre>  code()</pre><p>after</p></blockquote>")
    assert dom.select_one("blockquote pre code").get_text() == "  code()\n"
    assert len(dom.select("blockquote p")) == 2


def test_inline_formatting_breaks_and_code_delimiters():
    _, _, dom = _render(
        "<p><strong>bold</strong> <em>italic</em> <code>`x`  y</code> H<sub>2</sub>O x<sup>2</sup><br>next</p>"
    )
    assert dom.strong.get_text() == "bold"
    assert dom.em.get_text() == "italic"
    assert dom.code.get_text() == "`x`  y"
    assert dom.sub.get_text() == "2"
    assert dom.sup.get_text() == "2"
    assert dom.br is not None


def test_container_text_and_real_footer_named_section_are_not_lost():
    _, _, dom = _render(
        "<section>before<p>middle</p>after</section><h2>猜你喜欢</h2><p>real body</p>"
    )
    for text in ("before", "middle", "after", "real body"):
        assert text in dom.get_text()


def test_long_paragraph_reassembles_without_added_spaces_or_paragraphs():
    source = "word " * 300 + "终点"
    projection, _, dom = _render(f"<p>{source}</p>")
    assert len(projection.blocks) > 1
    assert len(dom.find_all("p")) == 1
    assert dom.p.get_text() == source


def test_link_labels_and_entities_remain_literal_even_with_html_enabled():
    _, _, dom = _render(
        '<p>&amp;copy; <a href="https://example.org">*literal* &lt;img src=x onerror=alert(1)&gt;</a></p>'
    )
    assert dom.img is None
    assert dom.em is None
    assert "&copy;" in dom.p.get_text()
    assert dom.a.get_text() == "*literal* <img src=x onerror=alert(1)>"


def test_export_deduplicates_title_and_serializes_url():
    article = _article("<h1>same title</h1><p>body</p>", title="same title")
    md = MarkdownExporter()._generate_markdown(article)
    assert md.count("# same title") == 1
    url_line = next(line for line in md.splitlines() if line.startswith("url: "))
    assert json.loads(url_line.removeprefix("url: ")) == str(article.url)


def test_entity_at_chunk_boundary_is_not_reinterpreted():
    source = "x" * 898 + "&copy;" + "y" * 100
    _, _, dom = _render(f"<p>{source.replace('&', '&amp;')}</p>")
    assert dom.p.get_text() == source


def test_complex_table_obeys_image_option():
    article = _article(
        '<table><tr><td colspan="2"><img src="https://example.org/a.png" alt="图"></td></tr></table>'
    )
    projection = ArticleReadingProjector().project(article)
    renderer = MarkdownReadingRenderer()
    assert "<img" not in renderer.render(projection)
    assert renderer.render(projection, include_images=True).count("<img") == 1


def test_wechat_per_line_code_wrappers_preserve_blank_lines():
    _, _, dom = _render(
        "<pre><code>def f():</code><br><code>    x = 1</code><br><code></code><br><code>    return x</code></pre>"
    )
    assert dom.code.get_text() == "def f():\n    x = 1\n\n    return x\n"


def test_plain_text_source_cannot_be_interpreted_as_html():
    source = 'x < y && literal <img src="x">'
    article = Article(
        url=ArticleURL.from_string("https://mp.weixin.qq.com/s/test"),
        content=ArticleContent.from_text(source),
    )
    projection = ArticleReadingProjector().project(article)
    md = MarkdownReadingRenderer().render(projection)
    dom = BeautifulSoup(MarkdownIt("commonmark", {"html": True}).render(md), "html.parser")
    assert dom.img is None
    assert dom.p.get_text() == source


@pytest.mark.parametrize(
    "source", ["1. item", "# heading", "- item", "---", "~~~", "a.b(c)!", "x | y"]
)
def test_literal_block_markers_render_without_added_backslashes(source):
    _, _, dom = _render(f"<p>{source}</p>")
    assert dom.p.get_text() == source
    assert dom.find(["ol", "ul", "hr", "h1", "pre"]) is None


def test_whitespace_around_emphasis_is_retained():
    _, _, dom = _render("<p>before<strong> bold </strong>after</p>")
    assert dom.p.get_text() == "before bold after"


def test_direct_container_text_ignores_html_indentation_and_comments():
    _, _, dom = _render(
        "<section><p>start</p><!-- invisible -->\n    direct <em>text</em>\n<p>end</p></section>"
    )
    assert dom.pre is None
    assert dom.em.get_text() == "text"
    assert "invisible" not in dom.get_text()
    assert [p.get_text().strip() for p in dom.find_all("p")] == ["start", "direct text", "end"]
