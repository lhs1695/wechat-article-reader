from datetime import datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest

from wechat_article_reader.domain.value_objects import ArticleURL
from wechat_article_reader.infrastructure.adapters.scrapers.wechat_httpx import WechatHttpxScraper
from wechat_article_reader.shared.exceptions import (
    ScraperBlockedError,
    ScraperError,
    ScraperRateLimitedError,
    ScraperTimeoutError,
)


@pytest.fixture
def scraper() -> WechatHttpxScraper:
    return WechatHttpxScraper(timeout=10, max_retries=1, min_request_interval_seconds=0)


def test_only_handles_wechat_urls(scraper: WechatHttpxScraper) -> None:
    assert scraper.can_handle(ArticleURL.from_string("https://mp.weixin.qq.com/s/demo"))
    assert not scraper.can_handle(ArticleURL.from_string("https://example.com/article"))


def test_scrapes_wechat_html(scraper: WechatHttpxScraper) -> None:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.text = """<h1 class="rich_media_title">测试标题</h1>
    <a id="js_name">测试公众号</a><div id="js_content"><p>安全的正文内容。</p></div>"""
    url = ArticleURL.from_string("https://mp.weixin.qq.com/s/demo")

    with patch(
        "wechat_article_reader.infrastructure.adapters.scrapers.wechat_httpx.safe_fetch_sync",
        return_value=response,
    ):
        article = scraper.scrape(url)

    assert article.title == "测试标题"
    assert article.account_name == "测试公众号"
    assert "安全的正文内容" in article.content.text


def test_normalizes_lazy_loaded_image_source(scraper: WechatHttpxScraper) -> None:
    article = scraper._parse_html(
        """<h1 class="rich_media_title">测试标题</h1>
        <div id="js_content"><img data-src="https://mmbiz.qpic.cn/lazy.jpg" alt="示意图"></div>""",
        ArticleURL.from_string("https://mp.weixin.qq.com/s/demo"),
    )

    assert 'src="https://mmbiz.qpic.cn/lazy.jpg"' in article.content_html
    assert "data-src" not in article.content_html
    assert article.content.images == ("https://mmbiz.qpic.cn/lazy.jpg",)


def test_keeps_existing_image_source_over_lazy_attributes(scraper: WechatHttpxScraper) -> None:
    article = scraper._parse_html(
        """<h1 class="rich_media_title">测试标题</h1>
        <div id="js_content"><img src="https://mmbiz.qpic.cn/original.jpg"
        data-src="https://mmbiz.qpic.cn/lazy.jpg"></div>""",
        ArticleURL.from_string("https://mp.weixin.qq.com/s/demo"),
    )

    assert article.content.images == ("https://mmbiz.qpic.cn/original.jpg",)


def test_rejects_unsafe_lazy_loaded_image_source(scraper: WechatHttpxScraper) -> None:
    article = scraper._parse_html(
        """<h1 class="rich_media_title">测试标题</h1>
        <div id="js_content"><p>安全正文</p><img data-src="javascript:alert(1)"></div>""",
        ArticleURL.from_string("https://mp.weixin.qq.com/s/demo"),
    )

    assert "javascript:" not in article.content_html
    assert article.content.images == ()


def test_rejects_http_image_that_web_csp_would_block(scraper: WechatHttpxScraper) -> None:
    article = scraper._parse_html(
        '<h1 class="rich_media_title">测试标题</h1><div id="js_content"><img src="http://mmbiz.qpic.cn/image.jpg"></div>',
        ArticleURL.from_string("https://mp.weixin.qq.com/s/demo"),
    )

    assert article.content.images == ()


def test_maps_wechat_block_to_stable_error(scraper: WechatHttpxScraper) -> None:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 403
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "blocked", request=MagicMock(), response=response
    )
    with (
        patch(
            "wechat_article_reader.infrastructure.adapters.scrapers.wechat_httpx.safe_fetch_sync",
            return_value=response,
        ),
        pytest.raises(ScraperBlockedError),
    ):
        scraper.scrape(ArticleURL.from_string("https://mp.weixin.qq.com/s/demo"))


def test_retries_rate_limit_using_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    limited = MagicMock(spec=httpx.Response)
    limited.status_code = 429
    limited.headers = {"Retry-After": "2"}
    success = MagicMock(spec=httpx.Response)
    success.status_code = 200
    success.raise_for_status.return_value = None
    success.text = '<h1 class="rich_media_title">标题</h1><div id="js_content">正文</div>'
    sleep = MagicMock()
    monkeypatch.setattr(
        "wechat_article_reader.infrastructure.adapters.scrapers.wechat_httpx.time.sleep", sleep
    )
    instance = WechatHttpxScraper(max_retries=2, min_request_interval_seconds=0)

    with patch(
        "wechat_article_reader.infrastructure.adapters.scrapers.wechat_httpx.safe_fetch_sync",
        side_effect=[limited, success],
    ) as fetch:
        article = instance.scrape(ArticleURL.from_string("https://mp.weixin.qq.com/s/demo"))

    assert article.title == "标题"
    assert fetch.call_count == 2
    assert sleep.call_count >= 1
    assert all(call.args[0] <= 0.1 for call in sleep.call_args_list)


def test_exhausted_rate_limit_has_distinct_error(scraper: WechatHttpxScraper) -> None:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 429
    response.headers = {}
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "limited", request=MagicMock(), response=response
    )
    with (
        patch(
            "wechat_article_reader.infrastructure.adapters.scrapers.wechat_httpx.safe_fetch_sync",
            return_value=response,
        ),
        pytest.raises(ScraperRateLimitedError) as exc_info,
    ):
        scraper.scrape(ArticleURL.from_string("https://mp.weixin.qq.com/s/demo"))

    assert exc_info.value.code == 2006


def test_rejects_missing_article_content(scraper: WechatHttpxScraper) -> None:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.text = "<html><title>空页面</title></html>"
    with (
        patch(
            "wechat_article_reader.infrastructure.adapters.scrapers.wechat_httpx.safe_fetch_sync",
            return_value=response,
        ),
        pytest.raises(ScraperError, match="无法提取文章内容"),
    ):
        scraper.scrape(ArticleURL.from_string("https://mp.weixin.qq.com/s/demo"))


def test_maps_timeout(scraper: WechatHttpxScraper) -> None:
    with (
        patch(
            "wechat_article_reader.infrastructure.adapters.scrapers.wechat_httpx.safe_fetch_sync",
            side_effect=httpx.TimeoutException("timed out"),
        ),
        pytest.raises(ScraperTimeoutError, match="请求超时"),
    ):
        scraper.scrape(ArticleURL.from_string("https://mp.weixin.qq.com/s/demo"))


def test_maps_verify_page_to_blocked_without_logging_body(
    scraper: WechatHttpxScraper, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "SECRET_ARTICLE_BODY_SHOULD_NOT_APPEAR"
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.text = f'<html><div id="verify_bar">请输入验证码 {secret}</div></html>'
    captured: list[str] = []

    def _capture(message: object, *args: object, **kwargs: object) -> None:
        captured.append(str(message))
        captured.extend(str(item) for item in args)
        captured.extend(str(value) for value in kwargs.values())

    monkeypatch.setattr(
        "wechat_article_reader.infrastructure.adapters.scrapers.wechat_httpx.logger.warning",
        _capture,
    )
    monkeypatch.setattr(
        "wechat_article_reader.infrastructure.adapters.scrapers.wechat_httpx.logger.debug",
        _capture,
    )

    with (
        patch(
            "wechat_article_reader.infrastructure.adapters.scrapers.wechat_httpx.safe_fetch_sync",
            return_value=response,
        ),
        pytest.raises(ScraperBlockedError),
    ):
        scraper.scrape(ArticleURL.from_string("https://mp.weixin.qq.com/s/demo"))

    joined = " ".join(captured)
    assert secret not in joined
    assert "请输入验证码" not in joined
    assert "kind=blocked" in joined


def test_parses_localized_publish_time(scraper: WechatHttpxScraper) -> None:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.text = """<h1 class="rich_media_title">测试标题</h1>
    <em id="publish_time">2024年1月15日 10:30</em>
    <div id="js_content"><p>安全的正文内容。</p></div>"""

    with patch(
        "wechat_article_reader.infrastructure.adapters.scrapers.wechat_httpx.safe_fetch_sync",
        return_value=response,
    ):
        article = scraper.scrape(ArticleURL.from_string("https://mp.weixin.qq.com/s/demo"))

    assert article.publish_time == datetime(2024, 1, 15, 10, 30)
