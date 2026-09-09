from __future__ import annotations

from types import SimpleNamespace

import pytest

from wechat_article_reader.application.use_cases.summarize_article import SummarizeArticleUseCase
from wechat_article_reader.domain.value_objects import ArticleContent
from wechat_article_reader.infrastructure.adapters.summarizers.deepseek import DeepSeekSummarizer
from wechat_article_reader.shared.exceptions import (
    SummarizerError,
    SummarizerNotAvailableError,
    SummarizerTokenLimitError,
)


def test_deepseek_returns_only_compact_summary_shape() -> None:
    summarizer = DeepSeekSummarizer("key", max_output_tokens=321)
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"overview":"内容","key_points":["要点"],"tags":["AI"],"one_sentence":"一句话"}'
                )
            )
        ]
    )
    summarizer._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: response))
    )
    summary = summarizer.summarize(ArticleContent.from_text("测试文章"))
    assert summary.overview == "内容"
    assert summary.key_points == ("要点",)
    assert summary.tags == ("AI",)
    assert "block_id" not in summarizer._build_prompt("测试", 500)


def test_deepseek_rejects_non_json_response() -> None:
    with pytest.raises(SummarizerError, match="有效 JSON"):
        DeepSeekSummarizer("key")._parse_response("## 摘要", 500)


def test_deepseek_shortens_overview_at_sentence_boundary() -> None:
    summary = DeepSeekSummarizer("key")._parse_response(
        '{"overview":"第一句完整。第二句仍在继续"}', 7
    )

    assert summary.overview == "第一句完整。\n\n> 内容已按长度限制收缩。"


def test_deepseek_retries_transient_error(monkeypatch) -> None:
    summarizer = DeepSeekSummarizer("key", max_retries=1)
    calls = 0

    class TransientError(Exception):
        status_code = 429

    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"overview":"内容"}'))]
    )

    def create(**_):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransientError()
        return response

    summarizer._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(
        "wechat_article_reader.infrastructure.adapters.summarizers.deepseek.time.sleep",
        lambda _: None,
    )
    assert summarizer._call_api("prompt")
    assert calls == 2


def test_summary_rejects_unavailable_summarizer(sample_article) -> None:
    summarizer = SimpleNamespace(is_available=lambda: False)
    use_case = SummarizeArticleUseCase(summarizer)
    with pytest.raises(SummarizerNotAvailableError):
        use_case.execute(sample_article)


def test_summary_rejects_overlong_article_without_calling_model(sample_article) -> None:
    summarizer = SimpleNamespace(
        is_available=lambda: True, summarize=lambda **_: pytest.fail("must not call model")
    )
    article = sample_article
    object.__setattr__(article, "content", ArticleContent.from_text("x" * 2_000))
    use_case = SummarizeArticleUseCase(summarizer, max_input_chars=1_000)
    with pytest.raises(SummarizerTokenLimitError, match="文章过长"):
        use_case.execute(article)
