from __future__ import annotations

from uuid import UUID

import pytest

from wechat_article_reader.domain.entities import Article
from wechat_article_reader.domain.value_objects import ArticleContent, ArticleURL
from wechat_article_reader.features.article_reading import (
    ArticlePageSizeError,
    ArticleReadingProjector,
    ArticleReadingService,
    ArticleStorageError,
    MarkdownReadingRenderer,
)


def _article(html: str, title: str = "阅读测试") -> Article:
    return Article(
        url=ArticleURL.from_string("https://mp.weixin.qq.com/s/reading-test"),
        title=title,
        content=ArticleContent.from_html(html),
    )


class _Storage:
    def __init__(self, article: Article | None = None, *, fail_save: bool = False) -> None:
        self.article = article
        self.fail_save = fail_save
        self.save_calls = 0

    def save(self, article: Article) -> None:
        self.save_calls += 1
        if self.fail_save:
            raise RuntimeError("write failed")
        if self.article is not None and str(self.article.url) == str(article.url):
            object.__setattr__(article, "id", self.article.id)
        self.article = article

    def get(self, article_id: UUID) -> Article | None:
        return self.article if self.article is not None and self.article.id == article_id else None

    def get_by_url(self, url: str) -> Article | None:
        return self.article if self.article is not None and str(self.article.url) == url else None


class _Workflow:
    def __init__(self, article: Article) -> None:
        self.article = article
        self.calls: list[tuple[str, bool]] = []

    def fetch_article(self, url: str, *, force_refresh: bool = False) -> Article:
        self.calls.append((url, force_refresh))
        return self.article


def test_projection_preserves_order_structure_and_safe_images() -> None:
    article = _article(
        """
        <div><h2>架构</h2><p>第一段</p>
        <blockquote>重要观点</blockquote><ul><li>步骤一</li></ul>
        <pre><code>print('ok')</code></pre>
        <table><tr><th>项</th><th>值</th></tr><tr><td>A</td><td>1</td></tr></table>
        <figure><img src="https://mmbiz.qpic.cn/a.png" alt="架构图"><figcaption>处理流程</figcaption></figure>
        <img src="javascript:alert(1)"></div>
        """
    )

    projection = ArticleReadingProjector().project(article)

    assert [block.type for block in projection.blocks] == [
        "heading",
        "paragraph",
        "quote",
        "list_item",
        "code",
        "table",
        "image",
    ]
    assert projection.blocks[-1].url == "https://mmbiz.qpic.cn/a.png"
    assert projection.blocks[-1].alt == "架构图"
    assert projection.blocks[-1].caption == "处理流程"
    assert projection.sections[0].title == "架构"
    assert projection == ArticleReadingProjector().project(article)


def test_renderer_outputs_model_friendly_markdown() -> None:
    projection = ArticleReadingProjector().project(
        _article('<h2>标题</h2><p>正文</p><img src="https://mmbiz.qpic.cn/a.png" alt="图">')
    )

    markdown = MarkdownReadingRenderer().render(projection)

    assert "## 标题" in markdown
    assert "正文" in markdown
    assert "![图](<https://mmbiz.qpic.cn/a.png>)" in markdown


def test_projection_retains_safe_inline_links_and_drops_unsafe_links() -> None:
    projection = ArticleReadingProjector().project(
        _article(
            "<p>阅读<a href='https://example.com/paper' title='论文'>官方论文</a>，"
            "并忽略<a href='javascript:alert(1)'>危险链接</a>。</p>"
        )
    )

    block = projection.blocks[0]
    markdown = MarkdownReadingRenderer().render(projection)

    assert block.text == "阅读官方论文，并忽略危险链接。"
    assert [link.url for link in block.links] == ["https://example.com/paper"]
    assert '[官方论文](<https://example.com/paper> "论文")' in markdown
    assert "javascript:" not in markdown


def test_projection_preserves_repeated_link_labels_without_rewriting_targets() -> None:
    projection = ArticleReadingProjector().project(
        _article(
            "<p><a href='https://example.com/one'>GitHub</a> 与普通 GitHub，"
            "再看<a href='https://example.com/two'>GitHub</a></p>"
        )
    )

    block = projection.blocks[0]

    assert block.markdown == (
        "[GitHub](<https://example.com/one>) 与普通 GitHub，再看[GitHub](<https://example.com/two>)"
    )
    assert [link.url for link in block.links] == [
        "https://example.com/one",
        "https://example.com/two",
    ]


def test_projection_retains_links_inside_tables() -> None:
    projection = ArticleReadingProjector().project(
        _article(
            "<table><tr><th>项目</th><th>地址</th></tr>"
            "<tr><td>Harness</td><td><a href='https://example.com/docs'>文档</a></td></tr></table>"
        )
    )

    block = projection.blocks[0]

    assert block.type == "table"
    assert "[文档](<https://example.com/docs>)" in (block.markdown or "")
    assert [link.url for link in block.links] == ["https://example.com/docs"]


def test_projection_retains_wechat_standalone_span_recommendation_links() -> None:
    """微信公众号常在文末用 span 包裹一组没有 p 标签的推荐链接。"""
    projection = ArticleReadingProjector().project(
        _article(
            "<section><span><a href='https://github.com/example/one'>项目一</a></span>"
            "<span><a href='https://github.com/example/two'>项目二</a></span>"
            "<span><a>无地址说明</a></span></section>"
        )
    )

    markdown = MarkdownReadingRenderer().render(projection)

    assert [block.text for block in projection.blocks] == ["项目一", "项目二", "无地址说明"]
    assert [link.url for block in projection.blocks for link in block.links] == [
        "https://github.com/example/one",
        "https://github.com/example/two",
    ]
    assert "[项目一](<https://github.com/example/one>)" in markdown
    assert "[项目二](<https://github.com/example/two>)" in markdown


def test_projection_retains_wechat_list_recommendation_links() -> None:
    """微信公众号的推荐阅读链接通常落在 li > section > span > a。"""
    projection = ArticleReadingProjector().project(
        _article(
            "<ul><li><section><span><a href='https://github.com/example/one'>项目一</a>：说明</span>"
            "</section></li><li><section><span><a href='https://github.com/example/two'>项目二</a>"
            "</span></section></li></ul>"
        )
    )

    blocks = projection.blocks

    assert [block.type for block in blocks] == ["list_item", "list_item"]
    assert [link.url for block in blocks for link in block.links] == [
        "https://github.com/example/one",
        "https://github.com/example/two",
    ]
    assert "[项目一](<https://github.com/example/one>)：说明" in blocks[0].markdown


def test_read_pages_are_continuous() -> None:
    paragraphs = "".join(f"<p>第{i}段 Python Agent 工程内容 {'文本' * 250}</p>" for i in range(6))
    article = _article(paragraphs)
    storage = _Storage(article)
    service = ArticleReadingService(_Workflow(article), storage)

    first = service.read(article.id, max_chars=1_000)
    second = service.read(article.id, cursor=first.next_cursor or 0, max_chars=1_000)

    assert first.has_more is True
    assert second.cursor == first.next_cursor
    assert first.content_markdown.startswith("# 阅读测试")
    assert first.block_count == 6
    assert first.chars == len(first.content_markdown)
    assert "第0段" in first.content_markdown
    assert "第0段" not in second.content_markdown


def test_text_only_reading_skips_images_and_keeps_cursor_continuous() -> None:
    article = _article(
        "<p>"
        + "甲" * 450
        + "</p><img src='https://mmbiz.qpic.cn/one.png'>"
        + "<p>"
        + "乙" * 450
        + "</p><img src='https://mmbiz.qpic.cn/two.png'>"
        + "<p>"
        + "丙" * 450
        + "</p>"
    )
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    first = service.read(article.id, max_chars=1_000, include_images=False)
    second = service.read(
        article.id, cursor=first.next_cursor or 0, max_chars=1_000, include_images=False
    )

    assert "![" not in first.content_markdown
    assert "![" not in second.content_markdown
    assert first.next_cursor == 4
    assert second.cursor == 4
    assert second.has_more is False
    assert first.content_markdown.count("甲") == 450
    assert first.content_markdown.count("乙") == 450
    assert second.content_markdown.count("丙") == 450


def test_text_only_reading_retains_image_caption_without_image_url() -> None:
    article = _article(
        "<figure><img src='https://mmbiz.qpic.cn/diagram.png' alt='架构图'>"
        "<figcaption>任务执行流程</figcaption></figure>"
    )
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    page = service.read(article.id, include_images=False)

    assert page.content_markdown == "# 阅读测试\n\n*任务执行流程*"
    assert "mmbiz.qpic.cn" not in page.content_markdown


def test_read_includes_oversized_block_when_within_hard_limit() -> None:
    article = _article("<p><a href='https://example.com/" + "a" * 1_100 + "'>链接</a></p>")
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    page = service.read(article.id, max_chars=1_000)

    assert page.has_more is False
    assert len(page.content_markdown) > 1_000


def test_read_rejects_block_larger_than_hard_limit() -> None:
    article = _article("<p><a href='https://example.com/" + "a" * 20_050 + "'>链接</a></p>")
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    with pytest.raises(ArticlePageSizeError, match="20000"):
        service.read(article.id, max_chars=8_000)


def test_ingest_uses_cache_and_refresh_preserves_article_id() -> None:
    cached = _article("<p>旧内容</p>")
    refreshed = _article("<p>新内容</p>")
    storage = _Storage(cached)
    workflow = _Workflow(refreshed)
    service = ArticleReadingService(workflow, storage)

    cached_result = service.ingest(str(cached.url))
    refreshed_result = service.ingest(str(cached.url), refresh=True)

    assert cached_result.cached is True
    assert cached_result.sections
    assert workflow.calls == [(str(cached.url), True)]
    assert refreshed_result.article_id == str(cached.id)


def test_ingest_cache_hit_does_not_write_or_rebuild_indexes() -> None:
    cached = _article("<p>缓存内容</p>")
    storage = _Storage(cached)
    service = ArticleReadingService(_Workflow(cached), storage)

    service.ingest(str(cached.url))

    assert storage.save_calls == 0


def test_reading_hash_changes_when_only_link_or_image_changes() -> None:
    first = ArticleReadingProjector().project(
        _article(
            "<p><a href='https://example.com/one'>文档</a></p><img src='https://mmbiz.qpic.cn/one.png'>"
        )
    )
    second = ArticleReadingProjector().project(
        _article(
            "<p><a href='https://example.com/two'>文档</a></p><img src='https://mmbiz.qpic.cn/two.png'>"
        )
    )

    assert first.blocks[0].text == second.blocks[0].text
    assert first.content_hash != second.content_hash
    assert first.blocks[0].id != second.blocks[0].id


def test_ingest_fails_when_durable_storage_fails() -> None:
    article = _article("<p>内容</p>")
    service = ArticleReadingService(_Workflow(article), _Storage(fail_save=True))

    with pytest.raises(ArticleStorageError, match="持久化失败"):
        service.ingest(str(article.url))


def test_read_does_not_split_list_group_across_pages() -> None:
    filler = "段" * 400
    item = "项" * 200
    article = _article(
        f"<p>{filler}</p><ul><li>{item}一</li><li>{item}二</li><li>{item}三</li></ul>"
    )
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    first = service.read(article.id, max_chars=1_000)
    second = service.read(article.id, cursor=first.next_cursor or 0, max_chars=1_000)

    assert "一" not in first.content_markdown
    assert all(marker in second.content_markdown for marker in ("一", "二", "三"))


def test_read_prefers_breaking_before_nested_heading() -> None:
    para = "正" * 500
    article = _article(f"<h2>第一部</h2><p>{para}</p><h3>细节</h3><p>{para}</p><p>{para}</p>")
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    first = service.read(article.id, max_chars=1_000)
    second = service.read(article.id, cursor=first.next_cursor or 0, max_chars=1_000)

    assert "第一部" in first.content_markdown
    assert "细节" not in first.content_markdown
    assert "### 细节" in second.content_markdown


def test_ingest_toc_defaults_to_h2_and_can_expand() -> None:
    article = _article("<h2>一部</h2><p>甲</p><h3>细目</h3><p>乙</p><h2>二部</h2><p>丙</p>")
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    compact = service.ingest(str(article.url))
    detailed = service.ingest(str(article.url), toc_level=3)

    assert [section.title for section in compact.sections] == ["一部", "二部"]
    assert [section.title for section in detailed.sections] == ["一部", "细目", "二部"]
    assert compact.sections[0].end_cursor == compact.sections[1].start_cursor
    assert compact.block_count == detailed.block_count == 6


def test_ingest_toc_aligns_to_shallowest_heading_when_article_has_no_h2() -> None:
    article = _article(
        "<h3>一、背景</h3><p>甲</p><h4>1.1 细节</h4><p>乙</p><h3>二、方案</h3><p>丙</p>"
    )
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    result = service.ingest(str(article.url))

    assert [section.title for section in result.sections] == ["一、背景", "二、方案"]
    assert "正文" not in [section.title for section in result.sections]
    assert result.sections[0].end_cursor == result.sections[1].start_cursor
    assert result.sections[0].end_cursor < result.block_count


def test_leading_copy_before_h3_is_preamble_not_whole_article() -> None:
    article = _article("<p>开场</p><h3>一、背景</h3><p>甲</p>")
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    result = service.ingest(str(article.url))

    assert [section.title for section in result.sections] == ["正文", "一、背景"]
    assert result.sections[0].end_cursor == result.sections[1].start_cursor
    assert result.sections[1].end_cursor == result.block_count


def test_first_page_section_title_stays_preamble_until_heading() -> None:
    article = _article("<p>开场</p><h3>一、背景</h3><p>甲</p>")
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    page = service.read(article.id, max_chars=1_000)

    assert page.section_title == "正文"
    assert page.section_end_cursor == 1
    assert "一、背景" in page.content_markdown
    assert page.to_dict()["section_end_cursor"] == 1


def test_headingless_continuation_does_not_inject_synthetic_title() -> None:
    para = "段" * 800
    article = _article(f"<p>{para}</p><p>{para}</p>")
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    first = service.read(article.id, max_chars=1_000)
    second = service.read(article.id, cursor=first.next_cursor or 0, max_chars=1_000)

    assert first.has_more is True
    assert first.section_title == "正文"
    assert second.section_title == "正文"
    assert not second.content_markdown.startswith("# 正文")
    assert second.section_end_cursor == first.block_count


def test_section_seek_stops_at_section_end() -> None:
    article = _article("<h2>甲节</h2><p>短</p><h2>乙节</h2><p>后面还有</p>")
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    page = service.read(article.id, section="甲节", max_chars=1_000)

    assert page.section_title == "甲节"
    assert page.section_end_cursor == 2
    assert page.has_more is False
    assert page.next_cursor is None
    assert "短" in page.content_markdown
    assert "后面还有" not in page.content_markdown


def test_section_seek_drops_next_heading_teasers_but_keeps_short_prose() -> None:
    article = _article(
        "<h3>第二阶段：骨架</h3><p>该用规则的地方用规则。</p>"
        "<p>05</p><p>PHASE THREE</p>"
        "<h3>第三阶段：执行</h3><p>后面还有</p>"
    )
    keep_short = _article(
        "<h3>设计逻辑</h3><p>不重复了。</p><h3>下一节</h3><p>乙</p>"
    )
    service = ArticleReadingService(_Workflow(article), _Storage(article))
    keep_service = ArticleReadingService(_Workflow(keep_short), _Storage(keep_short))

    sliced = service.read(article.id, section="第二阶段", max_chars=1_000)
    whole = service.read(article.id, max_chars=20_000)
    prose = keep_service.read(keep_short.id, section="设计逻辑", max_chars=1_000)

    assert sliced.has_more is False
    assert "该用规则的地方用规则" in sliced.content_markdown
    assert "PHASE THREE" not in sliced.content_markdown
    assert "后面还有" not in sliced.content_markdown
    assert "PHASE THREE" in whole.content_markdown
    assert "不重复了" in prose.content_markdown


def test_section_and_cursor_continue_inside_section_only() -> None:
    para = "段" * 800
    article = _article(f"<h2>甲节</h2><p>{para}</p><p>{para}</p><h2>乙节</h2><p>后面还有</p>")
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    first = service.read(article.id, section="甲节", max_chars=1_000)
    second = service.read(
        article.id, section="甲节", cursor=first.next_cursor or 0, max_chars=1_000
    )

    assert first.has_more is True
    assert "后面还有" not in first.content_markdown
    assert "后面还有" not in second.content_markdown
    assert second.section_title == "甲节"


def test_page_word_count_follows_projected_body_not_article() -> None:
    para = "段" * 800
    article = _article(f"<p>{para}</p><p>{para}</p>")
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    page = service.read(article.id, max_chars=1_000)

    assert page.has_more is True
    assert page.word_count == page.chars or page.word_count < page.chars
    assert page.word_count < article.word_count
    assert page.chars == len(page.content_markdown)


def test_read_fills_page_across_headings_instead_of_rewinding_to_last_h3() -> None:
    para = "段" * 400
    article = _article(f"<h3>一</h3><p>{para}</p><h3>二</h3><p>{para}</p><h3>三</h3><p>{para}</p>")
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    first = service.read(article.id, max_chars=1_000)

    assert "一" in first.content_markdown
    assert "二" in first.content_markdown
    assert first.chars > 700


def test_continuation_page_uses_current_section_not_later_heading() -> None:
    para = "正" * 500
    article = _article(f"<h3>三、实践</h3><p>{para}</p><p>{para}</p><h3>四、效果</h3><p>{para}</p>")
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    first = service.read(article.id, max_chars=1_000)
    second = service.read(article.id, cursor=first.next_cursor or 0, max_chars=1_000)

    assert first.has_more is True
    assert second.section_title == "三、实践"
    assert second.content_markdown.startswith("### 三、实践")
    assert second.content_markdown.count("### 四、效果") <= 1


def test_read_jumps_to_section_title_and_unique_prefix() -> None:
    article = _article("<h2>前言</h2><p>甲段</p><h2>实践总结</h2><p>乙段内容</p>")
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    exact = service.read(article.id, cursor=99, section="实践总结", max_chars=1_000)
    prefix = service.read(article.id, cursor=99, section="实践", max_chars=1_000)

    assert "乙段内容" in exact.content_markdown
    assert "甲段" not in exact.content_markdown
    assert exact.section_title == "实践总结"
    assert prefix.section_title == "实践总结"


def test_read_rejects_one_character_section_prefix() -> None:
    article = _article("<h2>结语</h2><p>乙段内容</p>")
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    with pytest.raises(ValueError, match="not found"):
        service.read(article.id, section="结")


def test_read_rejects_missing_or_ambiguous_section() -> None:
    article = _article("<h2>第一部分</h2><p>甲</p><h2>第一部</h2><p>乙</p>")
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    with pytest.raises(ValueError, match="not found"):
        service.read(article.id, section="不存在")
    with pytest.raises(ValueError, match="ambiguous"):
        service.read(article.id, section="第一")


def test_first_page_header_includes_author() -> None:
    article = _article("<p>开篇</p>", title="长文")
    object.__setattr__(article, "author", "黄迅")
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    page = service.read(article.id, max_chars=1_000)

    assert page.content_markdown.startswith("# 长文 · 黄迅")
    assert "开篇" in page.content_markdown


def test_first_page_does_not_repeat_title_when_body_starts_with_same_heading() -> None:
    article = _article("<h2>长文</h2><p>开篇</p>", title="长文")
    object.__setattr__(article, "author", "黄迅")
    service = ArticleReadingService(_Workflow(article), _Storage(article))

    page = service.read(article.id, max_chars=1_000)

    assert page.content_markdown.startswith("作者：黄迅")
    assert page.content_markdown.count("长文") == 1
    assert "## 长文" in page.content_markdown
