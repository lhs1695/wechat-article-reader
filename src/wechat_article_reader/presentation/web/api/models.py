"""API 请求/响应模型"""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from ....shared.constants import DEFAULT_SUMMARY_MAX_LENGTH


def _validate_http_url(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme not in {"http", "https"} or host != "mp.weixin.qq.com":
        raise ValueError("URL 必须是微信公众号文章地址")
    return value


class FetchRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    refresh: bool = False

    _validate_url = field_validator("url")(_validate_http_url)


class SummarizeRequest(BaseModel):
    article_id: str = Field(min_length=1, max_length=64)
    expected_revision: str = Field(min_length=1, max_length=128)
    max_length: int = Field(default=DEFAULT_SUMMARY_MAX_LENGTH, ge=50, le=10_000)


class ExportRequest(BaseModel):
    article_id: str = Field(min_length=1, max_length=64)
    expected_revision: str = Field(min_length=1, max_length=128)
    target: str = "markdown"
    skip_summary: bool = False
    include_body: bool = True
    include_images: bool | None = None
    download_images: bool = False


class BatchRequest(BaseModel):
    urls: list[str] = Field(default_factory=list, min_length=1, max_length=10)
    export_target: str = "markdown"
    skip_summary: bool = False
    include_body: bool = True

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, urls: list[str]) -> list[str]:
        for url in urls:
            if len(url) > 2048:
                raise ValueError("URL 最长为 2048 个字符")
            _validate_http_url(url)
        return urls


class FetchResponse(BaseModel):
    success: bool
    article_id: str = ""
    revision: str = ""
    reading_revision: str = ""
    projection_version: int = 0
    title: str = ""
    author: str = ""
    account_name: str = ""
    word_count: int = 0
    publish_time: str = ""
    content_html: str = ""
    content_notes: list[dict[str, str]] = Field(default_factory=list)
    export_path: str = ""
    download_url: str = ""
    error: str = ""


class SummarizeResponse(BaseModel):
    success: bool
    article_id: str = ""
    revision: str = ""
    reading_revision: str = ""
    summary: str = ""
    summary_html: str = ""
    key_points: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    one_sentence: str = ""
    content_notes: list[dict[str, str]] = Field(default_factory=list)
    error: str = ""


class HistoryItem(BaseModel):
    title: str
    url: str
    word_count: int = 0
    cached_at: str = ""
    summarized: bool = False


class StatusResponse(BaseModel):
    summarizers: list[str]
    exporters: list[str]
    total_cached: int


class HealthResponse(BaseModel):
    status: str
    deepseek_api: bool
    cache: bool
    optional_components: dict[str, str] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    error_code: int | str
    request_id: str
    recovery: dict[str, object] | None = None
    current_revision: str | None = None
    current_reading_revision: str | None = None
