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

    _validate_url = field_validator("url")(_validate_http_url)


class SummarizeRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    max_length: int = Field(default=DEFAULT_SUMMARY_MAX_LENGTH, ge=50, le=10_000)

    _validate_url = field_validator("url")(_validate_http_url)


class ExportRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    target: str = "markdown"
    skip_summary: bool = False
    summary_content: str = ""
    key_points: list[str] = Field(default_factory=list)
    include_body: bool = True

    _validate_url = field_validator("url")(_validate_http_url)


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
    title: str = ""
    author: str = ""
    account_name: str = ""
    word_count: int = 0
    publish_time: str = ""
    content_html: str = ""
    export_path: str = ""
    download_url: str = ""
    error: str = ""


class SummarizeResponse(BaseModel):
    success: bool
    summary: str = ""
    summary_html: str = ""
    key_points: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    one_sentence: str = ""
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
    error_code: int
    request_id: str
