from __future__ import annotations

import asyncio
import time
from threading import Event, Thread
from types import SimpleNamespace

import httpx
import pytest
from mcp.server.fastmcp import Context

from wechat_article_reader.application.use_cases.fetch_article import FetchArticleUseCase
from wechat_article_reader.domain.entities import Article
from wechat_article_reader.domain.value_objects import ArticleContent, ArticleURL
from wechat_article_reader.infrastructure.adapters.scrapers.wechat_httpx import (
    WechatHttpxScraper,
)
from wechat_article_reader.mcp.execution import (
    make_progress_callback,
    report_operation_phase,
    run_cancellable,
)
from wechat_article_reader.shared.exceptions import OperationDeadlineExceededError
from wechat_article_reader.shared.operation import BoundedKeyedLockPool, OperationSignal
from wechat_article_reader.shared.utils.ssrf_protection import _read_sync_response


def test_retry_wait_consumes_operation_budget() -> None:
    scraper = WechatHttpxScraper(max_retries=3, max_retry_delay_seconds=10)
    signal = OperationSignal(0.03)
    started = time.monotonic()

    with pytest.raises(OperationDeadlineExceededError):
        scraper._wait_before_retry(1, None, signal)

    assert time.monotonic() - started < 0.5


def test_expired_fetch_budget_prevents_late_cache_write() -> None:
    class Storage:
        writes: list[Article] = []

        def get_by_url(self, _url: str) -> None:
            return None

        def save(self, article: Article, *, cancel_event=None) -> None:
            self.writes.append(article)

    class SlowScraper:
        name = "slow"

        def can_handle(self, url: ArticleURL) -> bool:
            return url.is_wechat

        def scrape(self, url: ArticleURL, *, cancel_event=None) -> Article:
            time.sleep(0.04)
            return Article(url=url, content=ArticleContent.from_html("<p>晚到</p>"))

    storage = Storage()
    use_case = FetchArticleUseCase([SlowScraper()], storage=storage)
    signal = OperationSignal(0.02)

    with pytest.raises(OperationDeadlineExceededError):
        use_case.execute("https://mp.weixin.qq.com/s/slow", cancel_event=signal)

    assert storage.writes == []


def test_slow_response_stream_stops_at_operation_deadline() -> None:
    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"first"
            time.sleep(0.04)
            yield b"late"

        def close(self) -> None:
            pass

    signal = OperationSignal(0.02)
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://example.com"),
        stream=SlowStream(),
    )

    with pytest.raises(OperationDeadlineExceededError):
        _read_sync_response(
            response,
            max_response_bytes=None,
            allowed_content_types=None,
            cancel_event=signal,
        )
    assert response.is_closed


def test_keyed_locks_allow_unrelated_work_and_bound_waiters() -> None:
    pool = BoundedKeyedLockPool(max_keys=2)
    with pool.acquire("article-a"):
        started = Event()

        def acquire_other_key() -> None:
            with pool.acquire("article-b"):
                started.set()

        worker = Thread(target=acquire_other_key)
        worker.start()
        assert started.wait(1)
        worker.join(1)
        assert not worker.is_alive()

    with pool.acquire("same-key"):
        signal = OperationSignal(0.02)
        with pytest.raises(OperationDeadlineExceededError):
            pool.acquire("same-key", signal)


@pytest.mark.asyncio
async def test_run_cancellable_enforces_budget_and_signals_worker() -> None:
    stopped = Event()

    def worker(*, cancel_event) -> None:
        try:
            while not cancel_event.is_set():
                time.sleep(0.005)
        finally:
            stopped.set()

    with pytest.raises(OperationDeadlineExceededError):
        await run_cancellable(worker, operation_timeout=0.03)

    assert await asyncio.to_thread(stopped.wait, 1)


@pytest.mark.asyncio
async def test_mcp_progress_reports_phases_and_known_counts_without_percentages() -> None:
    notifications: list[dict[str, object]] = []

    class Session:
        async def send_progress_notification(self, **notification: object) -> None:
            notifications.append(notification)

    context = Context(
        request_context=SimpleNamespace(
            meta=SimpleNamespace(progressToken="test-token"),
            session=Session(),
            request_id=1,
        )
    )
    loop = asyncio.get_running_loop()
    await report_operation_phase(context, 1, "抓取文章")
    callback = make_progress_callback(context, loop)
    await asyncio.to_thread(callback, "下载图片", 2, 5)

    assert [item["progress"] for item in notifications] == [1, 2.0]
    assert [item["total"] for item in notifications] == [None, 5.0]
    assert [item["message"] for item in notifications] == ["抓取文章", "下载图片: 2/5"]
    assert all(item["progress_token"] == "test-token" for item in notifications)
