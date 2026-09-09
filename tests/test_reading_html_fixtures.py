from __future__ import annotations

from pathlib import Path
from uuid import UUID

from wechat_article_reader.domain.entities import Article
from wechat_article_reader.domain.value_objects import ArticleContent, ArticleURL
from wechat_article_reader.features.article_reading import (
    ArticleReadingProjector,
    ArticleReadingService,
    MarkdownReadingRenderer,
)
from wechat_article_reader.infrastructure.adapters.exporters.markdown import MarkdownExporter

_FIXTURE = Path(__file__).parent / "fixtures" / "html" / "wechat_dirty_dom.html"
_FOOTER_FIXTURE = Path(__file__).parent / "fixtures" / "html" / "wechat_account_footer.html"
_TABLE_LINK = "[单元格链接](<https://example.com/table-note>)"
_CODE_SNIPPET = 'return "wechat-dirty-dom"'
_RECOMMEND_ALPHA = "[相关文甲](<https://mp.weixin.qq.com/s/anon-alpha>)"
_RECOMMEND_BETA = "[相关文乙](<https://github.com/example/anon-reader>)"


def _article_from_fixture() -> Article:
    html = _FIXTURE.read_text(encoding="utf-8")
    return Article(
        url=ArticleURL.from_string("https://mp.weixin.qq.com/s/anon-dirty-dom"),
        title="匿名脏 DOM 样例",
        content=ArticleContent.from_html(html),
    )


class _Storage:
    def __init__(self, article: Article) -> None:
        self.article = article

    def save(self, article: Article) -> None:
        self.article = article

    def get(self, article_id: UUID) -> Article | None:
        return self.article if self.article.id == article_id else None

    def get_by_url(self, url: str) -> Article | None:
        return self.article if str(self.article.url) == url else None


class _Workflow:
    def __init__(self, article: Article) -> None:
        self.article = article

    def fetch_article(self, url: str, *, force_refresh: bool = False) -> Article:
        return self.article


def _pages(service: ArticleReadingService, article_id, *, max_chars: int = 1_000) -> list[str]:
    cursor = 0
    pages: list[str] = []
    while True:
        page = service.read(article_id, cursor=cursor, max_chars=max_chars)
        pages.append(page.content_markdown)
        if not page.has_more:
            return pages
        assert page.next_cursor is not None
        cursor = page.next_cursor


def test_dirty_dom_fixture_keeps_table_code_and_nested_recommend_links() -> None:
    article = _article_from_fixture()
    projection = ArticleReadingProjector().project(article)
    types = [block.type for block in projection.blocks]
    markdown = MarkdownReadingRenderer().render(projection)

    assert "table" in types
    assert "code" in types
    assert types.count("list_item") >= 5
    assert _TABLE_LINK in markdown
    assert _CODE_SNIPPET in markdown
    assert _RECOMMEND_ALPHA in markdown
    assert _RECOMMEND_BETA in markdown
    recommend_urls = [
        link.url for block in projection.blocks if block.type == "list_item" for link in block.links
    ]
    assert recommend_urls == [
        "https://mp.weixin.qq.com/s/anon-alpha",
        "https://github.com/example/anon-reader",
    ]


def test_dirty_dom_fixture_splits_oversized_copy_and_keeps_list_group() -> None:
    article = _article_from_fixture()
    projection = ArticleReadingProjector().project(article)
    placeholder_blocks = [
        block for block in projection.blocks if "合成占位句" in (block.text or "")
    ]
    assert len(placeholder_blocks) >= 2
    assert all(len(block.text) <= 900 for block in placeholder_blocks)

    service = ArticleReadingService(_Workflow(article), _Storage(article))
    pages = _pages(service, article.id)
    list_pages = [page for page in pages if "步骤甲" in page]

    assert len(pages) >= 2
    assert len(list_pages) == 1
    assert "步骤乙" in list_pages[0]
    assert "步骤丙" in list_pages[0]


def test_dirty_dom_fixture_uses_same_renderer_for_export_and_read_page(
    tmp_path: Path,
) -> None:
    article = _article_from_fixture()
    body = MarkdownReadingRenderer().render(
        ArticleReadingProjector().project(article), include_images=False
    )
    exported_path = Path(MarkdownExporter(output_dir=str(tmp_path)).export(article))
    exported = exported_path.read_text(encoding="utf-8")
    page = ArticleReadingService(_Workflow(article), _Storage(article)).read(
        article.id, max_chars=20_000
    )

    assert body in exported
    assert "## 原文内容" not in exported
    assert "![" not in exported
    assert "![" not in page.content_markdown
    assert _TABLE_LINK in page.content_markdown
    assert _CODE_SNIPPET in page.content_markdown
    assert _RECOMMEND_ALPHA in page.content_markdown
    assert page.content_markdown.count(_TABLE_LINK) == body.count(_TABLE_LINK)


def test_account_footer_wall_is_dropped_in_article_extend_links_stay() -> None:
    dirty = _article_from_fixture()
    footer_html = _FOOTER_FIXTURE.read_text(encoding="utf-8")
    footer_article = Article(
        url=ArticleURL.from_string("https://mp.weixin.qq.com/s/anon-footer-wall"),
        title="匿名推荐墙样例",
        content=ArticleContent.from_html(footer_html),
    )
    dirty_markdown = MarkdownReadingRenderer().render(ArticleReadingProjector().project(dirty))
    footer_markdown = MarkdownReadingRenderer().render(
        ArticleReadingProjector().project(footer_article)
    )
    footer_page = ArticleReadingService(_Workflow(footer_article), _Storage(footer_article)).read(
        footer_article.id, max_chars=20_000
    )

    assert _RECOMMEND_ALPHA in dirty_markdown
    assert "这是正文结论，应当保留。" in footer_markdown
    assert "今日好文推荐" not in footer_markdown
    assert "推荐文甲" not in footer_markdown
    assert "活动推销" not in footer_markdown
    assert "今日好文推荐" not in footer_page.content_markdown
    assert "这是正文结论，应当保留。" in footer_page.content_markdown
