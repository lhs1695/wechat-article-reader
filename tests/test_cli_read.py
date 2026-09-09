from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from click.testing import CliRunner

from wechat_article_reader.features.article_reading import ArticleReadPage, IngestResult
from wechat_article_reader.presentation.cli.app import cli


def test_read_url_outputs_markdown_and_next_cursor() -> None:
    service = Mock()
    service.ingest.return_value = IngestResult(
        article_id="6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        title="文章",
        word_count=100,
        image_count=0,
        cached=True,
    )
    service.read.return_value = ArticleReadPage(
        article_id="6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        cursor=0,
        next_cursor=3,
        has_more=True,
        content_markdown="## 标题\n\n正文",
    )

    with patch(
        "wechat_article_reader.presentation.cli.app.get_container",
        return_value=SimpleNamespace(article_reading_service=service),
    ):
        result = CliRunner().invoke(
            cli, ["read", "https://mp.weixin.qq.com/s/test", "--output-format", "text"]
        )

    assert result.exit_code == 0
    assert "## 标题" in result.output
    assert "--cursor 3" in result.output


def test_read_uuid_json_is_machine_readable() -> None:
    service = Mock()
    service.read.return_value = ArticleReadPage(
        article_id="6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        cursor=0,
        next_cursor=None,
        has_more=False,
        content_markdown="正文",
    )

    with patch(
        "wechat_article_reader.presentation.cli.app.get_container",
        return_value=SimpleNamespace(article_reading_service=service),
    ):
        result = CliRunner().invoke(
            cli,
            [
                "read",
                "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                "--section",
                "结语",
                "--output-format",
                "json",
            ],
        )

    assert result.exit_code == 0
    assert '"success": true' in result.output
    service.ingest.assert_not_called()
    service.read.assert_called_once_with(
        "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        cursor=0,
        max_chars=20_000,
        section="结语",
        include_images=False,
    )


def test_read_images_flag_requests_image_markdown() -> None:
    service = Mock()
    service.read.return_value = ArticleReadPage(
        article_id="6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        cursor=0,
        next_cursor=None,
        has_more=False,
        content_markdown="正文",
    )

    with patch(
        "wechat_article_reader.presentation.cli.app.get_container",
        return_value=SimpleNamespace(article_reading_service=service),
    ):
        result = CliRunner().invoke(
            cli,
            ["read", "6ba7b810-9dad-11d1-80b4-00c04fd430c8", "--images"],
        )

    assert result.exit_code == 0
    service.read.assert_called_once_with(
        "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        cursor=0,
        max_chars=20_000,
        section=None,
        include_images=True,
    )


def test_read_json_is_safe_for_gbk_stdout() -> None:
    service = Mock()
    service.read.return_value = ArticleReadPage(
        article_id="6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        cursor=0,
        next_cursor=None,
        has_more=False,
        content_markdown="含有不换行空格\u00a0的正文",
    )
    stream = _GbkStream()

    with (
        patch(
            "wechat_article_reader.presentation.cli.app.get_container",
            return_value=SimpleNamespace(article_reading_service=service),
        ),
        patch("sys.stdout", stream),
    ):
        result = CliRunner().invoke(
            cli,
            [
                "read",
                "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                "--output-format",
                "json",
            ],
        )

    assert result.exit_code == 0
    assert "\\u00a0" in result.output


class _GbkStream:
    encoding = "gbk"

    def write(self, value: str) -> int:
        value.encode("gbk")
        return len(value)

    def flush(self) -> None:
        return None
