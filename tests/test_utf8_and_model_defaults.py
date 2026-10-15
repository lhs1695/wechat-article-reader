from __future__ import annotations

from pathlib import Path

import pytest

from wechat_article_reader.infrastructure.adapters.summarizers.deepseek import DeepSeekSummarizer
from wechat_article_reader.infrastructure.config.settings import _parse_dotenv_file
from wechat_article_reader.shared import encoding
from wechat_article_reader.shared.constants import DEFAULT_DEEPSEEK_MODEL


def test_deepseek_default_uses_current_flash_api_model() -> None:
    assert DEFAULT_DEEPSEEK_MODEL == "deepseek-flash"
    assert DeepSeekSummarizer("test-key")._model == DEFAULT_DEEPSEEK_MODEL


def test_dotenv_parser_rejects_invalid_utf8_instead_of_dropping_bytes(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_bytes(b"WECHAT_ARTICLE_READER_LOG_LEVEL=INFO\n\xff")

    with pytest.raises(UnicodeDecodeError):
        _parse_dotenv_file(dotenv)


def test_standard_streams_are_reconfigured_as_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    class Stream:
        def __init__(self, name: str) -> None:
            self.name = name

        def reconfigure(self, **kwargs: str) -> None:
            calls.append((self.name, kwargs))

    monkeypatch.setattr(encoding.sys, "stdin", Stream("stdin"))
    monkeypatch.setattr(encoding.sys, "stdout", Stream("stdout"))
    monkeypatch.setattr(encoding.sys, "stderr", Stream("stderr"))

    encoding.configure_utf8_stdio()

    assert calls == [
        ("stdin", {"encoding": "utf-8", "errors": "strict"}),
        ("stdout", {"encoding": "utf-8", "errors": "backslashreplace"}),
        ("stderr", {"encoding": "utf-8", "errors": "backslashreplace"}),
    ]
