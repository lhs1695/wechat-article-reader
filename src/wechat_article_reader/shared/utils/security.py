"""安全工具：清理错误消息中的敏感信息。"""

from __future__ import annotations

import re


def sanitize_error_message(error_msg: str, sensitive_keys: list[str] | None = None) -> str:
    """清理错误消息中的敏感信息。"""
    if sensitive_keys is None:
        sensitive_keys = [
            "api_key",
            "api-key",
            "apikey",
            "token",
            "secret",
            "password",
            "passwd",
            "pwd",
            "credential",
        ]

    sanitized = error_msg
    for key in sensitive_keys:
        if key.lower() in sanitized.lower():
            pattern = rf"{key}[\s:=]+['\"]?([^\s'\"]+)['\"]?"
            sanitized = re.sub(
                pattern,
                f"{key}=***REDACTED***",
                sanitized,
                flags=re.IGNORECASE,
            )

    return sanitized
