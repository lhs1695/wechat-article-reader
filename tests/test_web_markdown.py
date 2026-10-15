from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from wechat_article_reader.application.use_cases.export_article import ExportArticleUseCase
from wechat_article_reader.application.use_cases.fetch_article import FetchArticleUseCase
from wechat_article_reader.application.use_cases.summarize_article import SummarizeArticleUseCase
from wechat_article_reader.domain.entities import Article, Summary
from wechat_article_reader.domain.value_objects import ArticleContent, ArticleURL
from wechat_article_reader.features.article_reading import SummarySourceRenderer
from wechat_article_reader.features.article_reading.service import ReadingRevisionChangedError
from wechat_article_reader.features.article_workflow import ArticleWorkflowService
from wechat_article_reader.features.article_workflow.dto import (
    ArticleExportPayload,
    ArticleMetadataPayload,
    ArticleSummaryPayload,
    BatchExportItemPayload,
    BatchExportPayload,
    SummaryPayload,
)
from wechat_article_reader.infrastructure.adapters.exporters.markdown import MarkdownExporter
from wechat_article_reader.infrastructure.persistence import (
    Database,
    SqlAlchemyStorage,
    upgrade_database,
)
from wechat_article_reader.presentation.web.api.endpoints import download_article, export_article
from wechat_article_reader.presentation.web.api.models import ExportRequest
from wechat_article_reader.presentation.web.app import create_app


def test_article_markdown_endpoint_reuses_reading_service() -> None:
    seen: list[bool] = []

    def read(article_id, cursor, max_chars, include_images=False, **kwargs):
        seen.append(include_images)
        return SimpleNamespace(
            to_dict=lambda: {
                "success": True,
                "article_id": article_id,
                "cursor": cursor,
                "content_markdown": "# 标题\n\n正文",
            }
        )

    reading = SimpleNamespace(
        read=read,
    )
    app = create_app(container=SimpleNamespace(article_reading_service=reading), prewarm=False)

    response = TestClient(app).get(
        "/api/article/markdown",
        params={
            "article_id": "article-1",
            "expected_reading_revision": "rp1:revision",
        },
    )

    assert response.status_code == 200
    assert response.json()["content_markdown"] == "# 标题\n\n正文"
    assert seen == [False]


def test_article_markdown_revision_error_keeps_request_id_and_recovery() -> None:
    def read(*_args, **_kwargs):
        raise ReadingRevisionChangedError("rp2:current-revision")

    reading = SimpleNamespace(read=read)
    app = create_app(container=SimpleNamespace(article_reading_service=reading), prewarm=False)

    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/article/markdown",
        params={
            "article_id": "article-1",
            "expected_reading_revision": "rp1:old-revision",
        },
        headers={"X-Request-ID": "web-contract-test"},
    )

    assert response.status_code == 409
    assert response.headers["X-Request-ID"] == "web-contract-test"
    assert response.json()["request_id"] == "web-contract-test"
    assert response.json()["error_code"] == "reading_changed"
    assert response.json()["recovery"]["action"] == "POST /api/article/fetch"


def test_summarize_endpoint_returns_complete_structured_summary() -> None:
    payload = ArticleSummaryPayload(
        article=ArticleMetadataPayload(
            url="https://mp.weixin.qq.com/s/example",
            title="测试文章",
            author=None,
            account_name=None,
            publish_time="",
            word_count=100,
            article_id="article-1",
            revision="article-revision",
        ),
        summary=SummaryPayload(
            overview="概述内容",
            key_points=("要点一", "要点二"),
            tags=("AI", "工程"),
            one_sentence="一句话结论",
        ),
    )
    requested_lengths: list[int] = []

    def summarize_cached(
        article_id: str, max_length: int, expected_revision: str
    ) -> ArticleSummaryPayload:
        assert article_id == "article-1"
        assert expected_revision == "article-revision"
        requested_lengths.append(max_length)
        return payload

    workflow = SimpleNamespace(summarize_cached=summarize_cached)
    reading = SimpleNamespace(
        get_metadata=lambda _: SimpleNamespace(
            revision="article-revision", reading_revision="rp1:revision"
        )
    )
    app = create_app(
        container=SimpleNamespace(
            article_workflow_service=workflow, article_reading_service=reading
        ),
        prewarm=False,
    )

    response = TestClient(app).post(
        "/api/article/summarize",
        json={"article_id": "article-1", "expected_revision": "article-revision"},
    )

    assert response.status_code == 200
    assert requested_lengths == [1_200]
    assert response.json() == {
        "success": True,
        "article_id": "article-1",
        "revision": "article-revision",
        "reading_revision": "rp1:revision",
        "content_notes": [],
        "summary": "概述内容",
        "summary_html": "<p>概述内容</p>\n",
        "key_points": ["要点一", "要点二"],
        "tags": ["AI", "工程"],
        "one_sentence": "一句话结论",
        "error": "",
    }


def test_home_page_describes_export_first_pipeline() -> None:
    app = create_app(container=SimpleNamespace(), prewarm=False)

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "统一 Markdown 投影" in response.text
    assert "默认无图" in response.text
    assert "分页 Markdown" not in response.text
    assert "去单篇贴链接" in response.text
    assert "可用导出器" not in response.text
    assert "可用摘要器" not in response.text


def test_article_page_labels_and_separates_tags_without_markdown_button() -> None:
    app = create_app(container=SimpleNamespace(), prewarm=False)

    response = TestClient(app).get("/article")

    assert response.status_code == 200
    assert '<h4 class="summary-tags-title">标签</h4>' in response.text
    assert ".join(' ');" in response.text
    assert "markdown-action" not in response.text
    assert "下载 Markdown 默认无图" in response.text
    assert "loading('抓取中', fetchHint)" in response.text
    assert "loading('生成摘要中', summarizeHint)" in response.text
    assert "DeepSeek 通常需要数十秒" in response.text
    assert "function loading(msg){" not in response.text


def test_history_page_uses_status_column() -> None:
    app = create_app(container=SimpleNamespace(), prewarm=False)

    response = TestClient(app).get("/history")

    assert response.status_code == 200
    assert "<th>状态</th>" in response.text
    assert "<th>操作</th>" not in response.text


def _metadata() -> ArticleMetadataPayload:
    return ArticleMetadataPayload(
        url="https://mp.weixin.qq.com/s/example",
        title="测试文章",
        author="作者",
        account_name="账号",
        publish_time="2026-01-01",
        word_count=100,
    )


def test_fetch_endpoint_returns_persisted_article_identity() -> None:
    article = SimpleNamespace(id="article-1", content_html="<p>正文</p>")
    metadata = SimpleNamespace(
        article_id="article-1",
        revision="article-revision",
        reading_revision="rp2:article-revision",
        projection_version=2,
        title="测试文章",
        author="作者",
        account_name="账号",
        word_count=100,
        publish_time="2026-01-01",
        content_notes=(),
    )
    calls: list[tuple[str, bool]] = []
    workflow = SimpleNamespace(
        fetch_article=lambda url, force_refresh=False: calls.append((url, force_refresh)) or article
    )
    reading = SimpleNamespace(get_metadata=lambda _: metadata)
    app = create_app(
        container=SimpleNamespace(
            article_workflow_service=workflow, article_reading_service=reading
        ),
        prewarm=False,
    )

    response = TestClient(app).post(
        "/api/article/fetch",
        json={"url": "https://mp.weixin.qq.com/s/example", "refresh": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["title"] == "测试文章"
    assert body["word_count"] == 100
    assert body["article_id"] == "article-1"
    assert body["reading_revision"] == "rp2:article-revision"
    assert calls == [("https://mp.weixin.qq.com/s/example", True)]


def test_export_endpoint_returns_download_url(tmp_path, monkeypatch) -> None:
    exported = tmp_path / "article.md"
    exported.write_text("body", encoding="utf-8")
    monkeypatch.setattr(
        "wechat_article_reader.presentation.web.api.endpoints._output_root",
        lambda: tmp_path,
    )
    payload = ArticleExportPayload(_metadata(), str(exported), True)
    seen: dict[str, object] = {}

    def export_cached(article_id, expected_revision, **kwargs):
        assert article_id == "article-1"
        assert expected_revision == "article-revision"
        seen.update(kwargs)
        return payload

    workflow = SimpleNamespace(export_cached=export_cached)
    app = create_app(container=SimpleNamespace(article_workflow_service=workflow), prewarm=False)

    response = TestClient(app).post(
        "/api/article/export",
        json={
            "article_id": "article-1",
            "expected_revision": "article-revision",
            "skip_summary": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["export_path"] == "article.md"
    assert "article.md" in body["download_url"]
    assert seen.get("include_images") is False


def test_web_download_exports_full_cached_summary_without_overwriting_it(tmp_path, monkeypatch):
    output_root = tmp_path / "output"
    output_root.mkdir()
    monkeypatch.setattr(
        "wechat_article_reader.presentation.web.api.endpoints._output_root",
        lambda: output_root,
    )
    database_path = tmp_path / "articles.db"
    upgrade_database(database_path)
    database = Database(database_path)
    storage = SqlAlchemyStorage(database)
    article = Article(
        url=ArticleURL.from_string("https://mp.weixin.qq.com/s/cached-summary"),
        title="缓存摘要导出",
        content=ArticleContent.from_html("<p>文章正文</p>"),
    )
    storage.save(article)
    summarize_use_case = SummarizeArticleUseCase(
        SimpleNamespace(
            is_available=lambda: True,
            summarize=lambda **_: pytest.fail("skip_summary 不应调用模型"),
            generation_profile={"model": "test-model"},
        ),
        SummarySourceRenderer(),
    )
    complete_summary = Summary(
        "完整服务端摘要",
        key_points=("完整要点一", "完整要点二"),
        tags=("服务端标签", "数据所有权"),
        one_sentence="服务端一句话结论",
        input_hash="current-body-hash",
        generation=summarize_use_case.generation_conditions(1_200),
    )
    assert storage.save_summary(article.id, article.revision, complete_summary)
    workflow = ArticleWorkflowService(
        FetchArticleUseCase([], storage=storage),
        summarize_use_case,
        ExportArticleUseCase({"markdown": MarkdownExporter(str(output_root))}),
        storage,
    )
    export_payloads = []
    export_workflow = workflow.export_cached

    def record_export(article_id, **options):
        payload = export_workflow(article_id, **options)
        export_payloads.append(payload)
        return payload

    workflow.export_cached = record_export
    try:
        container = SimpleNamespace(article_workflow_service=workflow)
        export_response = export_article(
            ExportRequest(
                article_id=str(article.id),
                expected_revision=article.revision,
                skip_summary=True,
            ),
            container,
        )
        assert export_response.success
        assert export_payloads[0].summarized
        file_id = parse_qs(urlparse(export_response.download_url).query)["file"][0]
        download_response = download_article(file_id)
        markdown = Path(download_response.path).read_text(encoding="utf-8")
        assert "完整服务端摘要" in markdown
        assert "完整要点一" in markdown and "完整要点二" in markdown
        assert "服务端一句话结论" in markdown
        assert "服务端标签" in markdown and "数据所有权" in markdown
        assert "客户端不完整摘要" not in markdown and "客户端要点" not in markdown
        assert storage.get(article.id).summary == complete_summary
    finally:
        database.dispose()


def test_batch_process_endpoint_maps_export_results(tmp_path, monkeypatch) -> None:
    exported = tmp_path / "batch.md"
    exported.write_text("body", encoding="utf-8")
    monkeypatch.setattr(
        "wechat_article_reader.presentation.web.api.endpoints._output_root",
        lambda: tmp_path,
    )
    payload = BatchExportPayload(
        1,
        1,
        (
            BatchExportItemPayload(
                "https://mp.weixin.qq.com/s/example",
                True,
                "测试文章",
                str(exported),
            ),
        ),
    )
    workflow = SimpleNamespace(batch_export=lambda urls, **_: payload)
    app = create_app(container=SimpleNamespace(article_workflow_service=workflow), prewarm=False)

    response = TestClient(app).post(
        "/api/batch/process",
        json={"urls": ["https://mp.weixin.qq.com/s/example"], "skip_summary": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body[0]["success"] is True
    assert body[0]["export_path"] == "batch.md"
