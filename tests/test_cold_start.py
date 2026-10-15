from __future__ import annotations

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        ("wechat_article_reader", "微信公众号文章阅读与摘要服务"),
        ("wechat_article_reader.mcp", "微信公众号文章阅读服务"),
    ],
)
def test_entrypoint_help_starts_in_fresh_process(module: str, expected: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp936"},
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert expected in result.stdout.lower()
