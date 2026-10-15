"""Web error contract and exception-to-HTTP mapping."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...features.article_reading import ArticlePageSizeError
from ...features.article_reading.service import ReadingRevisionChangedError
from ...shared.exceptions import (
    ArticleNotFoundError,
    ArticleVersionChangedError,
    ErrorCode,
    InvalidURLError,
    NetworkError,
    ScraperTimeoutError,
    SummarizerError,
    SummarizerNotAvailableError,
    SummarizerTokenLimitError,
    UseCaseError,
    ValidationError,
    WechatArticleReaderError,
)


def map_exception(exc: Exception, request_id: str) -> tuple[int, dict[str, Any]]:
    if isinstance(exc, ReadingRevisionChangedError):
        return (
            409,
            {
                "success": False,
                "error": str(exc),
                "error_code": "reading_changed",
                "request_id": request_id,
                "current_reading_revision": exc.current_reading_revision,
                "recovery": {"action": "POST /api/article/fetch", "refresh": False},
            },
        )
    if isinstance(exc, (ArticleVersionChangedError, ArticlePageSizeError, ValueError)):
        version_change = exc if isinstance(exc, ArticleVersionChangedError) else None
        return (
            409 if version_change is not None else 400,
            {
                "success": False,
                "error": str(exc),
                "error_code": "article_changed"
                if version_change is not None
                else "page_too_large"
                if isinstance(exc, ArticlePageSizeError)
                else "invalid_input",
                "request_id": request_id,
                **(
                    {
                        "current_revision": version_change.current_revision,
                        "recovery": {
                            "action": "POST /api/article/fetch",
                            "refresh": False,
                        },
                    }
                    if version_change is not None
                    else {}
                ),
            },
        )
    if isinstance(exc, ScraperTimeoutError):
        status_code = 504
    elif isinstance(exc, ArticleNotFoundError):
        status_code = 404
    elif isinstance(exc, SummarizerNotAvailableError):
        status_code = 503
    elif isinstance(
        exc, (InvalidURLError, ValidationError, UseCaseError, SummarizerTokenLimitError)
    ):
        status_code = 400
    elif isinstance(exc, (NetworkError, SummarizerError)):
        status_code = 502
    else:
        status_code = 500

    if isinstance(exc, WechatArticleReaderError):
        error_code = exc.code
        error = (
            exc.user_message
            if isinstance(
                exc,
                (
                    InvalidURLError,
                    ValidationError,
                    ArticleNotFoundError,
                    SummarizerTokenLimitError,
                    SummarizerNotAvailableError,
                ),
            )
            else exc.error_code.message
        )
    else:
        error_code = ErrorCode.UNKNOWN_ERROR.code
        error = "服务器内部错误"

    return status_code, {
        "success": False,
        "error": error,
        "error_code": error_code,
        "request_id": request_id,
    }


def validation_error_payload(
    request_id: str, details: Sequence[dict[str, Any]] | None = None
) -> dict[str, Any]:
    message = "请求参数无效"
    if details:
        first = details[0]
        location = tuple(str(item) for item in first.get("loc", ()) if item != "body")
        detail = str(first.get("msg", ""))
        if location and location[-1] == "url":
            message = "文章链接无效：仅支持 mp.weixin.qq.com 的 http(s) URL"
        elif detail:
            message = f"请求参数无效：{detail}"
    return {
        "success": False,
        "error": message,
        "error_code": ErrorCode.INVALID_INPUT.code,
        "request_id": request_id,
    }
