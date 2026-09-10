from types import SimpleNamespace

from fastapi.testclient import TestClient

from wechat_article_reader.features.article_workflow.dto import (
    ArticleExportPayload,
    ArticleFetchPayload,
    ArticleMetadataPayload,
    ArticleSummaryPayload,
    BatchExportItemPayload,
    BatchExportPayload,
    SummaryPayload,
)
from wechat_article_reader.presentation.web.app import create_app


def test_article_markdown_endpoint_reuses_reading_service() -> None:
    seen: list[bool] = []

    def read(article_id, cursor, max_chars, include_images=False):
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
        ingest=lambda url: SimpleNamespace(article_id="article-1"),
        read=read,
    )
    app = create_app(container=SimpleNamespace(article_reading_service=reading), prewarm=False)

    response = TestClient(app).get(
        "/api/article/markdown",
        params={"url": "https://mp.weixin.qq.com/s/example"},
    )

    assert response.status_code == 200
    assert response.json()["content_markdown"] == "# 标题\n\n正文"
    assert seen == [False]


def test_summarize_endpoint_returns_complete_structured_summary() -> None:
    payload = ArticleSummaryPayload(
        article=ArticleMetadataPayload(
            url="https://mp.weixin.qq.com/s/example",
            title="测试文章",
            author=None,
            account_name=None,
            publish_time="",
            word_count=100,
        ),
        summary=SummaryPayload(
            overview="概述内容",
            key_points=("要点一", "要点二"),
            tags=("AI", "工程"),
            one_sentence="一句话结论",
        ),
    )
    requested_lengths: list[int] = []

    def summarize(url: str, max_length: int) -> ArticleSummaryPayload:
        requested_lengths.append(max_length)
        return payload

    workflow = SimpleNamespace(summarize=summarize)
    app = create_app(container=SimpleNamespace(article_workflow_service=workflow), prewarm=False)

    response = TestClient(app).post(
        "/api/article/summarize",
        json={"url": "https://mp.weixin.qq.com/s/example"},
    )

    assert response.status_code == 200
    assert requested_lengths == [1_200]
    assert response.json() == {
        "success": True,
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


def test_fetch_endpoint_returns_article_metadata() -> None:
    payload = ArticleFetchPayload(
        **_metadata().__dict__,
        overview="正文",
        content_html="<p>正文</p>",
        content_truncated=False,
    )
    workflow = SimpleNamespace(fetch=lambda url, content_limit=None: payload)
    app = create_app(container=SimpleNamespace(article_workflow_service=workflow), prewarm=False)

    response = TestClient(app).post(
        "/api/article/fetch",
        json={"url": "https://mp.weixin.qq.com/s/example"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["title"] == "测试文章"
    assert body["word_count"] == 100


def test_export_endpoint_returns_download_url(tmp_path, monkeypatch) -> None:
    exported = tmp_path / "article.md"
    exported.write_text("body", encoding="utf-8")
    monkeypatch.setattr(
        "wechat_article_reader.presentation.web.api.endpoints._output_root",
        lambda: tmp_path,
    )
    payload = ArticleExportPayload(_metadata(), str(exported), True)
    seen: dict[str, object] = {}

    def export(url, **kwargs):
        seen.update(kwargs)
        return payload

    workflow = SimpleNamespace(export=export)
    app = create_app(container=SimpleNamespace(article_workflow_service=workflow), prewarm=False)

    response = TestClient(app).post(
        "/api/article/export",
        json={"url": "https://mp.weixin.qq.com/s/example", "skip_summary": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["export_path"] == "article.md"
    assert "article.md" in body["download_url"]
    assert seen.get("include_images") is False


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
