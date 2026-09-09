"""MCP 工具参数输入验证。"""

from __future__ import annotations

import re
from urllib.parse import urlparse


class MCPValidationError(Exception):
    """MCP 输入验证异常"""


class MCPInputValidator:
    ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})
    INVISIBLE_UNICODE_PATTERN: re.Pattern[str] = re.compile(
        r"[\u200b-\u200f\u2028-\u202f\u2060-\u206f\ufeff\u00ad]"
    )

    @classmethod
    def validate_url(cls, url: str) -> str:
        if not url or not isinstance(url, str):
            raise MCPValidationError("URL must be a non-empty string")

        url = url.strip()
        if len(url) > 2048:
            raise MCPValidationError(f"URL too long: {len(url)} > 2048")

        url = cls.INVISIBLE_UNICODE_PATTERN.sub("", url)
        parsed = urlparse(url)

        if parsed.scheme not in cls.ALLOWED_SCHEMES:
            raise MCPValidationError(f"Disallowed URL scheme: {parsed.scheme!r}")

        if not parsed.hostname:
            raise MCPValidationError("URL missing hostname")
        hostname = parsed.hostname.casefold()
        if hostname != "mp.weixin.qq.com" and not hostname.endswith(".mp.weixin.qq.com"):
            raise MCPValidationError("Only WeChat article URLs are supported")

        netloc = parsed.netloc
        if any(ch in set(";|&`$(){}!~") for ch in netloc):
            raise MCPValidationError("Suspicious character in URL hostname")
        if any(c.isspace() for c in netloc):
            raise MCPValidationError("Invalid whitespace in URL hostname")
        if any(ch in set("\"'<>\\^") for ch in netloc):
            raise MCPValidationError("Invalid character in URL hostname")

        from ..shared.utils.ssrf_protection import SSRFBlockedError, SSRFSafeTransport

        try:
            SSRFSafeTransport.validate_url(url)
        except SSRFBlockedError as e:
            raise MCPValidationError(f"URL blocked by SSRF protection: {e}") from e

        return url

    @classmethod
    def validate_article_id(cls, article_id: str) -> str:
        from uuid import UUID

        text = cls.sanitize_text(article_id, 100)
        try:
            return str(UUID(text))
        except (TypeError, ValueError) as exc:
            raise MCPValidationError("article_id must be a UUID") from exc

    @classmethod
    def sanitize_text(cls, text: str, max_length: int = 10_000) -> str:
        if not isinstance(text, str):
            raise MCPValidationError("Text must be a string")
        if len(text) > max_length:
            raise MCPValidationError(f"Input too long: {len(text)} > {max_length}")
        text = text.replace("\x00", "")
        text = cls.INVISIBLE_UNICODE_PATTERN.sub("", text)
        return "".join(c for c in text if c.isprintable() or c in "\n\r\t")

    @classmethod
    def validate_max_length(cls, value: int, lower: int = 50, upper: int = 10_000) -> int:
        if not isinstance(value, int) or value < lower or value > upper:
            raise MCPValidationError(
                f"max_length must be integer in [{lower}, {upper}], got {value}"
            )
        return value

    @classmethod
    def validate_int_range(
        cls,
        value: int,
        *,
        field_name: str,
        lower: int,
        upper: int,
    ) -> int:
        if not isinstance(value, int):
            raise MCPValidationError(f"{field_name} must be an integer")
        if value < lower or value > upper:
            raise MCPValidationError(f"{field_name} must be in [{lower}, {upper}]")
        return value

    @classmethod
    def validate_toc_level(cls, value: int) -> int:
        return cls.validate_int_range(value, field_name="toc_level", lower=1, upper=6)

    @classmethod
    def validate_optional_section(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise MCPValidationError("section must be a string")
        text = cls.sanitize_text(value, 200).strip()
        return text or None
