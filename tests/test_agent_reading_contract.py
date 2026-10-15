from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt
from sqlalchemy import event

from wechat_article_reader.application.use_cases.fetch_article import FetchArticleUseCase
from wechat_article_reader.application.use_cases.summarize_article import SummarizeArticleUseCase
from wechat_article_reader.domain.entities import Article, Summary
from wechat_article_reader.domain.value_objects import ArticleContent, ArticleURL
from wechat_article_reader.features.article_reading import (
    ArticlePageSizeError,
    ArticleReadingProjector,
    ArticleReadingService,
    ArticleStorageError,
    MarkdownReadingRenderer,
    SummarySourceRenderer,
)
from wechat_article_reader.features.article_workflow import ArticleWorkflowService
from wechat_article_reader.infrastructure.persistence import (
    Database,
    SqlAlchemyStorage,
    upgrade_database,
)
from wechat_article_reader.mcp.error_mapping import raise_mcp_error
from wechat_article_reader.mcp.errors import MCPToolError
from wechat_article_reader.mcp.execution import run_cancellable
from wechat_article_reader.mcp.input_validator import MCPInputValidator, export_root_context
from wechat_article_reader.shared.exceptions import (
    ArticleVersionChangedError,
    OperationCancelledError,
    StorageError,
)
from wechat_article_reader.shared.utils.article_identity import normalize_article_url


def article(html: str, *, title: str = "测试") -> Article:
    return Article(
        url=ArticleURL.from_string("https://mp.weixin.qq.com/s/contract"),
        title=title,
        content=ArticleContent.from_html(html),
    )


def reader(item: Article) -> ArticleReadingService:
    return ArticleReadingService(
        SimpleNamespace(), SimpleNamespace(get=lambda _: item, get_by_url=lambda _: item)
    )


def render(html: str, *, images: bool = False) -> BeautifulSoup:
    markdown = MarkdownReadingRenderer().render(
        ArticleReadingProjector().project(article(html)), include_images=images
    )
    return BeautifulSoup(
        MarkdownIt("commonmark", {"html": True}).enable("strikethrough").render(markdown),
        "html.parser",
    )


@pytest.fixture
def storage(tmp_path):
    path = tmp_path / "contract.db"
    upgrade_database(path)
    database = Database(path)
    try:
        yield SqlAlchemyStorage(database)
    finally:
        database.dispose()


def test_parent_section_includes_children_but_stops_before_peer():
    item = article("<h2>父章</h2><p>前言</p><h3>子章</h3><p>子内容</p><h2>下一章</h2><p>后文</p>")
    service = reader(item)
    page = service.read(item.id, section="父章")
    assert "子内容" in page.content_markdown
    assert "后文" not in page.content_markdown
    assert page.reading_scope == "section" and not page.end_of_article
    assert [s.title for s in page.sections_in_page] == ["父章", "子章"]
    assert service.get_metadata(item.id).sections[0].end_cursor == page.section_end_cursor


def test_duplicate_long_and_invisible_headings_have_positional_selection():
    item = article(
        "<h2>同名</h2><p>第一处</p><h2>同名</h2><p>第二处</p><h2>零\u200b宽 标题</h2><p>末尾</p>"
    )
    service = reader(item)
    with pytest.raises(ValueError, match="section_start_cursor"):
        service.read(item.id, section="同名")
    second = service.get_metadata(item.id).sections[1]
    assert (
        "第二处" in service.read(item.id, section_start_cursor=second.start_cursor).content_markdown
    )
    assert "末尾" in service.read(item.id, section="零宽 标题").content_markdown
    long_item = article(f"<h2>{'标题' * 600}</h2><p>正文</p>")
    assert "正文" in reader(long_item).read(long_item.id, section_start_cursor=0).content_markdown


@pytest.mark.parametrize("argument", ["cursor", "max_chars", "section_start_cursor"])
def test_booleans_are_not_reading_integers(argument):
    item = article("<h2>标题</h2><p>正文</p>")
    with pytest.raises(ValueError):
        reader(item).read(item.id, **{argument: True})


def test_revision_bound_next_read_and_section_eof():
    item = article("<h2>章节</h2>" + "<p>" + "文字 " * 1800 + "</p>")
    service = reader(item)
    first = service.read(item.id, max_chars=1000, section="章节")
    assert first.next_read is not None
    next_page = service.read(**first.next_read)
    assert next_page.cursor == first.next_cursor
    with pytest.raises(ValueError, match="expected_revision"):
        service.read(item.id, cursor=first.next_cursor)
    object.__setattr__(item, "revision", str(uuid4()))
    with pytest.raises(ArticleVersionChangedError):
        service.read(**first.next_read)
    eof = service.read(
        item.id, cursor=first.section_end_cursor, section="章节", expected_revision=item.revision
    )
    assert not eof.has_more and eof.word_count == 0


def test_image_metadata_and_inline_order_are_explicit():
    item = article('<p>图片前<img src="https://mmbiz.qpic.cn/a.png" alt="图解">图片后</p>')
    service = reader(item)
    no_image = service.read(item.id)
    with_image = service.read(item.id, include_images=True)
    assert no_image.image_count == no_image.omitted_images == 1
    assert with_image.image_count == 1 and with_image.omitted_images == 0
    assert not no_image.image_content_read and not with_image.image_content_read
    assert "未读取" in with_image.content_notes[-1]
    body = with_image.content_markdown
    assert body.index("图片前") < body.index("![图解]") < body.index("图片后")


def test_malformed_media_and_link_urls_do_not_break_article_reading():
    item = article(
        '<p>前文<img src="https://[invalid"><a href="https://[invalid">链接文字</a>后文</p>'
    )
    page = reader(item).read(item.id, include_images=True)
    assert "前文" in page.content_markdown and "后文" in page.content_markdown
    assert "链接文字" in page.content_markdown and page.image_count == 0


@pytest.mark.parametrize(
    "html, literal",
    [
        ("<p><span>#</span><span> 标题</span></p>", "# 标题"),
        ("<p><span>1.</span><span> 列表</span></p>", "1. 列表"),
        ("<p><span>-</span><span> 列表</span></p>", "- 列表"),
        ("<p><span>~</span><span>~删除~~</span></p>", "~~删除~~"),
    ],
)
def test_split_spans_do_not_create_markdown_structure(html, literal):
    dom = render(html)
    assert not dom.find(["h1", "ol", "ul", "del"])
    assert dom.get_text().strip() == literal


def test_code_nonbreaking_spaces_and_independent_list_starts():
    dom = render(
        '<pre><code>x = "a&nbsp;b"\n    print(x)</code></pre><ol start="3"><li>甲</li></ol><ol start="7"><li>乙</li></ol>'
    )
    assert dom.code.get_text() == 'x = "a\xa0b"\n    print(x)\n'
    assert [node.get("start") for node in dom.find_all("ol")] == ["3", "7"]


def test_relative_links_and_css_semantics_survive_sanitization():
    dom = render(
        '<p><a href="#part">跳章</a><a href="/s/other">另一篇</a><b>粗体</b><i>斜体</i><span style="font-weight:700;font-style:italic;text-decoration:underline;color:red">样式</span></p>'
    )
    assert [node["href"] for node in dom.find_all("a")] == [
        "https://mp.weixin.qq.com/s/contract#part",
        "https://mp.weixin.qq.com/s/other",
    ]
    assert "粗体" in dom.strong.get_text() and "斜体" in dom.em.get_text()
    assert dom.find("u") is not None and not dom.find(attrs={"style": True})


def test_decorations_and_footer_conclusions_are_preserved():
    item = article(
        '<h2>章节</h2><p>正文</p><p>42</p><p>SQL</p><p>PHASETHREE</p><div id="js_bottom_ad_area"><p>推荐阅读：这是正文结论</p></div>'
    )
    page = reader(item).read(item.id, section="章节")
    for text in ["42", "SQL", "PHASETHREE", "这是正文结论"]:
        assert text in page.content_markdown


def test_words_are_not_cut_and_terminal_heading_does_not_add_page():
    item = article("<p>" + "abcde " * 500 + "</p><h2>终章</h2>")
    projection = ArticleReadingProjector().project(item)
    paragraphs = [block.text for block in projection.blocks if block.type == "paragraph"]
    assert all(
        piece.strip().startswith("abcde") and piece.strip().endswith("abcde")
        for piece in paragraphs
    )
    page = reader(item).read(item.id)
    assert not page.has_more and "终章" in page.content_markdown


def test_hard_budget_includes_preamble():
    item = article("<pre><code>" + "x" * 19950 + "</code></pre>", title="标题" * 100)
    with pytest.raises(ArticlePageSizeError):
        reader(item).read(item.id)


def test_projection_is_reused_then_invalidated(monkeypatch):
    item = article("<p>初版</p>")
    service = reader(item)
    calls = 0
    original = service._projector.project

    def project(value):
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(service._projector, "project", project)
    service.get_metadata(item.id)
    service.read(item.id)
    assert calls == 1
    item.update_content(ArticleContent.from_html("<p>新版</p>"))
    object.__setattr__(item, "revision", str(uuid4()))
    assert "新版" in service.read(item.id).content_markdown and calls == 2
    assert len(service._projection_cache) == 1


def test_whitespace_refresh_invalidates_summary_and_preserves_first_cache_time(storage):
    first = article("<pre><code>if x:\n    y()</code></pre>")
    first.attach_summary(Summary("原摘要"))
    storage.save(first)
    updated = article("<pre><code>if x:\n        y()</code></pre>")
    storage.save(updated)
    loaded = storage.get(first.id)
    assert loaded.id == updated.id == first.id
    assert loaded.created_at == first.created_at
    assert loaded.revision != first.revision and loaded.summary is None
    assert not storage.save_summary(first.id, first.revision, Summary("过期摘要"))
    assert "        y()" in storage.get(first.id).content_html


def test_summary_race_cannot_restore_old_body(storage):
    item = article("<p>初版正文</p>")
    storage.save(item)

    def summarize(*args, **kwargs):
        storage.save(article("<p>刷新正文</p>"))
        return Summary("过期摘要")

    adapter = SimpleNamespace(is_available=lambda: True, summarize=summarize)
    workflow = ArticleWorkflowService(
        SimpleNamespace(),
        SummarizeArticleUseCase(adapter, SummarySourceRenderer()),
        storage=storage,
    )
    with pytest.raises(ArticleVersionChangedError):
        workflow.summarize_existing(item)
    current = storage.get(item.id)
    assert "刷新正文" in current.content_text and current.summary is None


def test_html_only_update_does_not_rebind_existing_summary(storage):
    item = article("<pre><code>if x:\n    y()</code></pre>")
    item.attach_summary(Summary("原摘要"))
    storage.save(item)
    item.update_content(ArticleContent.from_html("<pre><code>if x:\n        y()</code></pre>"))
    storage.save(item)
    assert storage.get(item.id).summary is None


def test_identical_summary_conditions_reuse_but_changed_length_regenerates(storage, monkeypatch):
    item = article("<p>测试正文</p>")
    storage.save(item)
    calls = []

    def summarize(*args, **kwargs):
        calls.append(kwargs)
        return Summary("摘要")

    adapter = SimpleNamespace(
        is_available=lambda: True, summarize=summarize, generation_profile={"model": "test"}
    )
    workflow = ArticleWorkflowService(
        SimpleNamespace(execute=lambda _url: item),
        SummarizeArticleUseCase(adapter, SummarySourceRenderer()),
        storage=storage,
    )
    first = workflow.summarize_existing(item)
    # URL processing and the cached MCP path share the same persisted summary.
    from_url = workflow.process(str(item.url))
    second = workflow.summarize_cached(str(item.id))
    assert len(calls) == 1 and first.summary.generated_at == second.summary.generated_at
    assert from_url.summary.generated_at == first.summary.generated_at
    workflow.summarize_existing(item, max_length=700)
    assert len(calls) == 2
    assert storage.get(item.id).summary.generation["max_length"] == 700
    adapter.generation_profile = {"model": "test-v2"}
    workflow.process(str(item.url))
    assert len(calls) == 3
    assert storage.get(item.id).summary.generation["model"] == "test-v2"
    original_conditions = workflow._summarize_use_case.generation_conditions
    monkeypatch.setattr(
        workflow._summarize_use_case,
        "generation_conditions",
        lambda length: {**original_conditions(length), "prompt_version": "structured-v3"},
    )
    workflow.summarize_cached(str(item.id))
    assert len(calls) == 4
    assert storage.get(item.id).summary.generation["prompt_version"] == "structured-v3"


def test_cancel_after_flush_rolls_back(storage):
    signal = Event()

    def cancel_after_sql(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("INSERT INTO ARTICLES"):
            signal.set()

    event.listen(storage.database.engine, "after_cursor_execute", cancel_after_sql)
    item = article("<p>不得写入</p>")
    original_id = item.id
    try:
        with pytest.raises(OperationCancelledError):
            storage.save(item, cancel_event=signal)
    finally:
        event.remove(storage.database.engine, "after_cursor_execute", cancel_after_sql)
    assert storage.get(item.id) is None
    assert item.id == original_id


@pytest.mark.parametrize("failure_stage", ["flush", "commit"])
@pytest.mark.parametrize("has_existing", [False, True])
def test_failed_article_save_restores_entity_and_keeps_old_cache(
    storage, monkeypatch, failure_stage, has_existing
):
    from wechat_article_reader.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork

    old = article("<p>旧缓存</p>", title="旧标题")
    updated = article("<p>新内容</p>", title="新标题")
    updated.attach_summary(Summary("旧版摘要"))
    if has_existing:
        storage.save(old)
    original_state = (
        updated.id,
        updated.revision,
        updated.created_at,
        updated.updated_at,
        updated.summary,
    )

    if failure_stage == "flush":

        def fail_article_write(
            _connection, _cursor, statement, _parameters, _context, _executemany
        ):
            if statement.lstrip().upper().startswith(("INSERT INTO ARTICLES", "UPDATE ARTICLES")):
                raise RuntimeError("injected flush failure")

        event.listen(storage.database.engine, "before_cursor_execute", fail_article_write)
    else:

        def fail_commit(_self):
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", fail_commit)

    try:
        with pytest.raises(StorageError):
            storage.save(updated)
    finally:
        if failure_stage == "flush":
            event.remove(storage.database.engine, "before_cursor_execute", fail_article_write)

    assert (
        updated.id,
        updated.revision,
        updated.created_at,
        updated.updated_at,
        updated.summary,
    ) == original_state
    cached = storage.get_by_url(str(old.url))
    if has_existing:
        assert cached is not None
        assert cached.title == "旧标题"
        assert cached.content_text == "旧缓存"
    else:
        assert cached is None


def test_concurrent_cold_fetches_share_one_cached_identity(storage):
    from threading import Barrier

    class ConcurrentScraper:
        name = "concurrent"
        barrier = Barrier(2)

        def can_handle(self, url: ArticleURL) -> bool:
            return url.is_wechat

        def scrape(self, url: ArticleURL, *, cancel_event=None) -> Article:
            self.barrier.wait(timeout=5)
            return Article(
                url=url, title="并发抓取", content=ArticleContent.from_html("<p>正文</p>")
            )

    use_case = FetchArticleUseCase(scrapers=[ConcurrentScraper()], storage=storage)
    url = "https://mp.weixin.qq.com/s/contract"
    with ThreadPoolExecutor(max_workers=2) as executor:
        articles = list(executor.map(lambda _: use_case.execute(url), range(2)))

    assert articles[0].id == articles[1].id
    assert storage.get_by_url(url).id == articles[0].id
    assert storage.get_stats().total_entries == 1


def test_refresh_persistence_failure_is_not_reported_as_ingest_success(storage):
    from wechat_article_reader.features.article_workflow import ArticleWorkflowService

    previous = article("<p>持久化正文</p>", title="已缓存标题")
    storage.save(previous)

    class RefreshScraper:
        name = "refresh"

        def can_handle(self, url: ArticleURL) -> bool:
            return url.is_wechat

        def scrape(self, url: ArticleURL, *, cancel_event=None) -> Article:
            return Article(
                url=url,
                title="未持久化的新标题",
                content=ArticleContent.from_html("<p>新正文</p>"),
            )

    def fail_update(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("UPDATE ARTICLES"):
            raise RuntimeError("injected flush failure")

    fetch = FetchArticleUseCase(scrapers=[RefreshScraper()], storage=storage)
    workflow = ArticleWorkflowService(fetch, summarize_use_case=None)
    reading = ArticleReadingService(workflow, storage)
    event.listen(storage.database.engine, "before_cursor_execute", fail_update)
    try:
        with pytest.raises(ArticleStorageError, match="写入 SQLite 缓存失败"):
            reading.ingest(str(previous.url), refresh=True)
    finally:
        event.remove(storage.database.engine, "before_cursor_execute", fail_update)

    current = storage.get_by_url(str(previous.url))
    assert current is not None
    assert current.id == previous.id
    assert current.revision == previous.revision
    assert current.title == "已缓存标题"
    assert current.content_text == "持久化正文"


@pytest.mark.asyncio
async def test_cancellation_signals_running_worker_before_late_write():
    started, release, finished = Event(), Event(), Event()
    writes = []

    def worker(*, cancel_event):
        started.set()
        try:
            release.wait(3)
            if cancel_event.is_set():
                raise OperationCancelledError()
            writes.append("late write")
        finally:
            finished.set()

    task = asyncio.create_task(run_cancellable(worker))
    assert await asyncio.to_thread(started.wait, 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    assert await asyncio.to_thread(finished.wait, 3)
    assert not writes


def test_url_parameter_order_identity_and_offline_cache(monkeypatch):
    left = "https://mp.weixin.qq.com/s?mid=1&__biz=2&scene=3"
    right = "https://mp.weixin.qq.com/s?__biz=2&mid=1"
    assert normalize_article_url(left) == normalize_article_url(right)
    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *a, **k: pytest.fail("cached input must not resolve DNS")
    )
    assert MCPInputValidator.validate_url(left)
    item = article("<p>离线正文</p>")
    assert reader(item).ingest(left).cached


def test_oversized_error_has_executable_export_recovery(tmp_path):
    item = article("<p>正文</p>")
    token = export_root_context.set(tmp_path)
    try:
        with pytest.raises(MCPToolError) as error:
            raise_mcp_error(ArticlePageSizeError("too large"), article_id=str(item.id), cursor=7)
        recovery = error.value.recovery
        assert recovery["tool"] == "export_article"
        arguments = recovery["arguments"]
        assert MCPInputValidator.validate_article_id(arguments["article_id"]) == str(item.id)
        assert MCPInputValidator.validate_export_path(arguments["path"]) == arguments["path"]
        assert recovery["blocked_cursor"] == 7
    finally:
        export_root_context.reset(token)


def test_projection_cache_respects_entry_and_byte_limits():
    service = reader(article("<p>正文</p>"))
    service._projection_max_entries = 2
    for index in range(3):
        service._project(article(f"<p>正文{index}</p>"))
    assert len(service._projection_cache) == 2
    assert service._projection_bytes <= service._projection_max_bytes
    service._projection_max_bytes = 1
    previous_count = len(service._projection_cache)
    # An individually oversized projection is never retained.
    service._project(article("<p>" + "大" * 1000 + "</p>"))
    assert len(service._projection_cache) == previous_count
