from __future__ import annotations

import pytest

from wechat_article_reader.application.use_cases.fetch_article import FetchArticleUseCase
from wechat_article_reader.domain.value_objects import ArticleURL
from wechat_article_reader.shared.exceptions import (
    InvalidURLError,
    ScraperBlockedError,
    UseCaseError,
)


class _BlockedScraper:
    name = "wechat_httpx"

    def can_handle(self, url: ArticleURL) -> bool:
        return url.is_wechat

    def scrape(self, url: ArticleURL, *, cancel_event=None):
        raise ScraperBlockedError("抓取被反爬限制")


def test_fetch_rejects_non_wechat_url() -> None:
    use_case = FetchArticleUseCase(scrapers=[])

    with pytest.raises(InvalidURLError, match="微信公众号"):
        use_case.execute("https://example.com/article")


def test_fetch_wechat_without_scraper_stays_use_case_error() -> None:
    use_case = FetchArticleUseCase(scrapers=[])

    with pytest.raises(UseCaseError, match="没有可用的抓取器"):
        use_case.execute("https://mp.weixin.qq.com/s/demo")


def test_fetch_logs_error_type_not_body(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    def _capture(message: object, *args: object, **kwargs: object) -> None:
        captured.append(str(message))
        captured.extend(str(item) for item in args)
        captured.extend(str(value) for value in kwargs.values())

    monkeypatch.setattr(
        "wechat_article_reader.application.use_cases.fetch_article.logger.warning",
        _capture,
    )
    monkeypatch.setattr(
        "wechat_article_reader.application.use_cases.fetch_article.logger.info",
        _capture,
    )

    with pytest.raises(ScraperBlockedError):
        FetchArticleUseCase(scrapers=[_BlockedScraper()]).execute("https://mp.weixin.qq.com/s/demo")

    joined = " ".join(captured)
    assert "kind=not_wechat" not in joined
    assert "ScraperBlockedError" in joined
    assert "抓取被反爬限制" not in joined
    assert "js_content" not in joined
