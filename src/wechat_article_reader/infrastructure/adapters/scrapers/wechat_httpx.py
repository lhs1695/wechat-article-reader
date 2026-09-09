"""微信公众号HTTPX抓取器 - 快速模式"""

import asyncio
import random
import re
import time
from datetime import datetime
from threading import Lock
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from ....domain.entities import Article, ArticleSource
from ....domain.value_objects import ArticleContent, ArticleURL
from ....shared.constants import USER_AGENTS, WECHAT_CONTENT_SELECTORS
from ....shared.exceptions import (
    OperationCancelledError,
    ScraperBlockedError,
    ScraperError,
    ScraperRateLimitedError,
    ScraperTimeoutError,
)
from ....shared.utils.html_safety import sanitize_html
from ....shared.utils.ssrf_protection import (
    ResponseTooLargeError,
    SSRFBlockedError,
    UnsafeContentTypeError,
    safe_fetch_sync,
)
from .base import BaseScraper


class WechatHttpxScraper(BaseScraper):
    """
    微信公众号HTTPX抓取器

    使用HTTPX进行快速HTTP请求，适用于大部分微信文章。

    设计要点：
    - 仅对“网络层异常”（连接/超时等）进行重试
    - HTTP 状态码错误（如 404/403）不盲目重试，便于快速失败与定位
    - 使用实例级 httpx.Client 复用连接池，避免每次请求重建 TCP 连接
    """

    def __init__(
        self,
        timeout: int = 30,
        max_retries: int = 3,
        proxy: str | None = None,
        user_agent_rotation: bool = True,
        max_response_bytes: int = 5 * 1024 * 1024,
        max_content_chars: int = 500_000,
        min_request_interval_seconds: float = 0.5,
        max_retry_delay_seconds: float = 10,
    ):
        self._timeout = timeout
        self._max_retries = max_retries
        self._user_agent_rotation = user_agent_rotation
        self._max_response_bytes = max_response_bytes
        self._max_content_chars = max_content_chars
        self._min_request_interval_seconds = min_request_interval_seconds
        self._max_retry_delay_seconds = max_retry_delay_seconds
        self._request_lock = Lock()
        self._last_request_started = 0.0
        if proxy:
            logger.warning("出于 SSRF 安全考虑，文章抓取不会使用已配置的代理")

    @property
    def name(self) -> str:
        return "wechat_httpx"

    def can_handle(self, url: ArticleURL) -> bool:
        """只处理微信公众号链接"""
        return url.is_wechat

    def scrape(
        self, url: ArticleURL, *, cancel_event: "CancellationSignal | None" = None
    ) -> Article:
        """抓取微信公众号文章"""
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelledError()
        logger.debug(f"开始抓取: {url}")

        headers = {
            "User-Agent": self._choose_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }

        try:
            response = self._fetch_with_retry(str(url), headers=headers, cancel_event=cancel_event)
        except httpx.TimeoutException as e:
            raise ScraperTimeoutError(f"请求超时: {e}") from e
        except SSRFBlockedError as e:
            raise ScraperBlockedError(f"SSRF防护拦截：{e}") from e
        except httpx.TransportError as e:
            raise ScraperError(f"网络错误: {e}") from e
        except (ResponseTooLargeError, UnsafeContentTypeError) as e:
            raise ScraperError(str(e)) from e

        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelledError()
        # 仅在请求成功后再做状态码检查（避免把 4xx/5xx 当成“网络异常”参与重试）
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status in (401, 403):
                raise ScraperBlockedError(f"请求被拒绝 (HTTP {status})") from e
            if status == 429:
                raise ScraperRateLimitedError("请求被限流 (HTTP 429)，请稍后重试") from e
            raise ScraperError(f"HTTP错误 (HTTP {status})") from e

        # 解析HTML
        return self._parse_html(response.text, url)

    def _choose_user_agent(self) -> str:
        if self._user_agent_rotation:
            return random.choice(USER_AGENTS)
        return USER_AGENTS[0]

    def close(self) -> None:
        """关闭资源（当前无持久连接）"""
        return

    def _get(self, url: str, headers: dict[str, str]) -> httpx.Response:
        try:
            return safe_fetch_sync(
                url,
                method="GET",
                headers=headers,
                timeout=self._timeout,
                proxy=None,
                max_redirects=5,
                max_response_bytes=self._max_response_bytes,
                allowed_content_types={"text/html", "application/xhtml+xml"},
            )
        except SSRFBlockedError as e:
            raise ScraperBlockedError(f"SSRF防护拦截：{e}") from e

    def _fetch_with_retry(
        self,
        url: str,
        *,
        headers: dict[str, str],
        cancel_event: "CancellationSignal | None",
    ) -> httpx.Response:
        """Retry transient transport/429/5xx failures with bounded local pacing."""
        for attempt in range(1, max(1, self._max_retries) + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelledError()
            self._wait_for_request_slot(cancel_event)
            try:
                response = self._get(url, headers)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == max(1, self._max_retries):
                    raise
                self._wait_before_retry(attempt, None, cancel_event)
                continue
            if (
                response.status_code == 429 or 500 <= response.status_code <= 599
            ) and attempt < max(1, self._max_retries):
                self._wait_before_retry(attempt, response, cancel_event)
                continue
            return response
        raise AssertionError("unreachable retry loop")

    def _wait_for_request_slot(self, cancel_event: "CancellationSignal | None" = None) -> None:
        if self._min_request_interval_seconds <= 0:
            return
        with self._request_lock:
            remaining = self._min_request_interval_seconds - (
                time.monotonic() - self._last_request_started
            )
            if remaining > 0:
                self._wait_with_cancellation(remaining, cancel_event)
            self._last_request_started = time.monotonic()

    def _wait_before_retry(
        self,
        attempt: int,
        response: httpx.Response | None,
        cancel_event: "CancellationSignal | None",
    ) -> None:
        delay = min(float(2 ** (attempt - 1)), self._max_retry_delay_seconds)
        if response is not None:
            raw_retry_after = response.headers.get("Retry-After")
            try:
                if raw_retry_after is not None:
                    delay = min(max(float(raw_retry_after), 0), self._max_retry_delay_seconds)
            except ValueError:
                pass
        logger.warning(f"抓取第 {attempt} 次遇到暂时性失败，{delay:.1f} 秒后重试")
        self._wait_with_cancellation(delay, cancel_event)

    @staticmethod
    def _wait_with_cancellation(delay: float, cancel_event: "CancellationSignal | None") -> None:
        deadline = time.monotonic() + delay
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelledError()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.1))

    def _parse_html(self, html: str, url: ArticleURL) -> Article:
        """解析HTML内容"""
        soup = BeautifulSoup(html, "html.parser")

        # 提取标题
        title = self._extract_title(soup)

        # 提取作者/公众号名称
        author, account_name = self._extract_author(soup)

        # 提取发布时间
        publish_time = self._extract_publish_time(soup, html)

        # 提取正文内容
        extracted_content = self._extract_content(soup)
        content_html = sanitize_html(self._normalize_image_sources(extracted_content))
        if not content_html:
            raise ScraperError("无法提取文章内容")

        # 创建内容对象
        content = ArticleContent.from_html(content_html)
        if len(content.text) > self._max_content_chars:
            raise ScraperError("文章正文超过允许的最大长度")

        # 创建来源
        source = ArticleSource.wechat(
            account_name=account_name or "未知",
        )

        return Article(
            url=url,
            title=title,
            author=author,
            account_name=account_name,
            publish_time=publish_time,
            content=content,
            source=source,
        )

    def _extract_title(self, soup: BeautifulSoup) -> str:
        """提取标题"""
        # 尝试多种方式
        # bs4 stubs 对 attrs 的 value 类型要求较宽（str/bytes/Pattern/...），
        # 这里用 Any 简化静态类型兼容。
        selectors: list[tuple[str, dict[str, Any] | None]] = [
            ("h1", {"class": "rich_media_title"}),
            ("h1", {"id": "activity-name"}),
            ("meta", {"property": "og:title"}),
            ("title", None),
        ]

        for tag, attrs in selectors:
            elem = soup.find(tag, attrs=attrs) if attrs is not None else soup.find(tag)
            if elem:
                if tag == "meta":
                    content_val = elem.get("content")
                    if isinstance(content_val, list):
                        content_val = content_val[0] if content_val else ""
                    return str(content_val or "").strip()
                return elem.get_text(strip=True)

        return "无标题"

    def _extract_author(self, soup: BeautifulSoup) -> tuple[str | None, str | None]:
        """提取作者和公众号名称"""
        author = None
        account_name = None

        # 公众号名称
        name_elem = soup.find("a", {"id": "js_name"}) or soup.find(
            "span", {"class": "rich_media_meta_nickname"}
        )
        if name_elem:
            account_name = name_elem.get_text(strip=True)

        # 作者
        author_elem = soup.find("span", {"class": "rich_media_meta_text"})
        if author_elem:
            author = author_elem.get_text(strip=True)
            # 微信模板有时嵌套两个同名 span，get_text 会粘成 "Yixin MengYixin Meng"
            if author and len(author) % 2 == 0:
                half = len(author) // 2
                if author[:half] == author[half:]:
                    author = author[:half]

        return author, account_name

    def _extract_publish_time(self, soup: BeautifulSoup, html: str) -> datetime | None:
        """提取发布时间"""
        # 方式1: 从script中提取日期字符串
        patterns = [
            r'var createTime = "(\d{4}-\d{2}-\d{2})"',
            r'"create_time":\s*"(\d{4}-\d{2}-\d{2})"',
            r'publish_time\s*=\s*"(\d{4}-\d{2}-\d{2})"',
        ]

        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                try:
                    return datetime.strptime(match.group(1), "%Y-%m-%d")
                except ValueError:
                    pass

        # 方式2: 从元素中提取
        time_elem = soup.find("em", {"id": "publish_time"})
        if time_elem:
            time_text = time_elem.get_text(strip=True)
            for date_format in (
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d",
                "%Y年%m月%d日 %H:%M",
                "%Y年%m月%d日",
            ):
                try:
                    return datetime.strptime(time_text, date_format)
                except ValueError:
                    continue

        # 方式3: Unix 时间戳变量（微信新版本）
        ts_patterns = [
            r'create_time\s*=\s*["\']?(\d{10})',
            r'(?:^|[\s;,\'"])ct\s*=\s*["\']?(\d{10})',
        ]
        for pattern in ts_patterns:
            match = re.search(pattern, html)
            if match:
                try:
                    ts = int(match.group(1))
                    if ts > 1000000000:  # 有效 Unix 时间戳范围
                        return datetime.fromtimestamp(ts)
                except (ValueError, OSError):
                    pass

        return None

    def _extract_content(self, soup: BeautifulSoup) -> str:
        """提取正文内容"""
        # 尝试多个选择器
        for selector in WECHAT_CONTENT_SELECTORS:
            if selector.startswith("#"):
                elem = soup.find(id=selector[1:])
            elif selector.startswith("."):
                elem = soup.find(class_=selector[1:])
            else:
                elem = soup.select_one(selector)

            if elem:
                # 去掉微信行号列表(<ul class="code-snippet__line-index">)
                for ul in elem.find_all("ul", class_="code-snippet__line-index"):
                    ul.decompose()
                html = str(elem)
                # BeautifulSoup 渲染会丢掉 <br/>，手动补回相邻 code 标签间的换行
                html = re.sub(r"(</code>)\s*(<code)", r"\1<br>\2", html)
                return html

        return ""

    @staticmethod
    def _normalize_image_sources(html: str) -> str:
        """Convert WeChat lazy-loaded image URLs to safe standard ``src`` attributes."""
        soup = BeautifulSoup(html, "html.parser")
        for image in soup.find_all("img"):
            candidates = (
                image.get("src"),
                image.get("data-src"),
                image.get("data-original"),
            )
            source = next(
                (
                    normalized
                    for candidate in candidates
                    if (normalized := WechatHttpxScraper._normalize_image_url(candidate))
                    is not None
                ),
                None,
            )
            if source is not None:
                image["src"] = source
            elif "src" in image.attrs:
                del image["src"]

            # The delivery HTML uses only the standard attribute. The sanitization
            # step that follows remains the single security boundary for rendering.
            for attribute in ("data-src", "data-original"):
                if attribute in image.attrs:
                    del image[attribute]
        return str(soup)

    @staticmethod
    def _normalize_image_url(value: object) -> str | None:
        """Accept only browser-loadable remote image URLs, including protocol-relative URLs."""
        if isinstance(value, list):
            value = value[0] if value else ""
        candidate = str(value or "").strip()
        if candidate.startswith("//"):
            candidate = f"https:{candidate}"
        parsed = urlsplit(candidate)
        if parsed.scheme == "https" and parsed.netloc:
            return candidate
        return None

    async def scrape_async(
        self, url: ArticleURL, *, cancel_event: "CancellationSignal | None" = None
    ) -> Article:
        """Use the same paced, cancellable retry policy as the synchronous path."""
        return await asyncio.to_thread(self.scrape, url, cancel_event=cancel_event)


class CancellationSignal(Protocol):
    def is_set(self) -> bool: ...
