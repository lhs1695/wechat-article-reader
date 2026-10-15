from __future__ import annotations

from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

from wechat_article_reader.application.use_cases.summarize_article import SummarizeArticleUseCase
from wechat_article_reader.domain.value_objects import ArticleContent
from wechat_article_reader.features.article_reading import SummarySourceRenderer
from wechat_article_reader.features.article_reading import projection as projection_module
from wechat_article_reader.infrastructure.adapters.summarizers.deepseek import DeepSeekSummarizer
from wechat_article_reader.shared.exceptions import (
    OperationCancelledError,
    SummarizerError,
    SummarizerNotAvailableError,
    SummarizerTokenLimitError,
    UseCaseError,
)


def _status_error(error_type, status_code: int):
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx.Response(status_code, request=request)
    return error_type("SDK error", response=response, body=None)


def test_sdk_retry_is_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "wechat_article_reader.infrastructure.adapters.summarizers.deepseek.OpenAI",
        lambda **kwargs: calls.append(kwargs) or SimpleNamespace(),
    )
    DeepSeekSummarizer("key")._get_client()
    assert calls[0]["max_retries"] == 0


def test_real_openai_sdk_transient_and_permanent_errors_are_classified():
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    transient_errors = (
        APIConnectionError(request=request),
        APITimeoutError(request=request),
        _status_error(RateLimitError, 429),
        _status_error(InternalServerError, 503),
    )
    permanent_errors = (
        _status_error(BadRequestError, 400),
        _status_error(BadRequestError, 401),
    )

    assert all(DeepSeekSummarizer._is_transient(error) for error in transient_errors)
    assert not any(DeepSeekSummarizer._is_transient(error) for error in permanent_errors)


@pytest.mark.parametrize("reason", ["length", "content_filter", None])
def test_noncompleted_generation_is_rejected(reason):
    adapter = DeepSeekSummarizer("key")
    response = SimpleNamespace(choices=[SimpleNamespace(finish_reason=reason)])
    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: response))
    )
    with pytest.raises(SummarizerError, match="未正常完成"):
        adapter._call_api("prompt")


def test_missing_structured_fields_are_rejected():
    with pytest.raises(SummarizerError):
        DeepSeekSummarizer("key")._parse_response('{"overview":"partial"}', 500)


def test_cancelled_retry_does_not_call_model_again():
    signal, calls = Event(), []

    def create(**kwargs):
        calls.append(kwargs)
        signal.set()
        raise _status_error(RateLimitError, 429)

    adapter = DeepSeekSummarizer("key")
    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    with pytest.raises(OperationCancelledError):
        adapter._call_api("prompt", cancel_event=signal)
    assert len(calls) == 1


def test_total_budget_caps_request_timeout_and_prevents_retry():
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        raise _status_error(RateLimitError, 429)

    adapter = DeepSeekSummarizer("key", total_timeout=0.2)
    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    with pytest.raises(SummarizerError, match="总耗时"):
        adapter._call_api("prompt")
    assert len(calls) == 1 and 0 < calls[0]["timeout"] <= 0.200001


def test_deepseek_returns_only_compact_summary_shape() -> None:
    summarizer = DeepSeekSummarizer("key", max_output_tokens=321)
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content='{"overview":"内容","key_points":["要点"],"tags":["AI"],"one_sentence":"一句话"}'
                ),
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
        '{"overview":"第一句完整。第二句仍在继续","key_points":[],"tags":[],"one_sentence":""}', 7
    )

    assert summary.overview == "第一句完整。\n\n> 内容已按长度限制收缩。"


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: APIConnectionError(
            request=httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        ),
        lambda: _status_error(RateLimitError, 429),
        lambda: _status_error(InternalServerError, 503),
    ],
)
def test_deepseek_retries_transient_sdk_error(monkeypatch, error_factory) -> None:
    summarizer = DeepSeekSummarizer("key", max_retries=1)
    calls = 0
    request_error = error_factory()

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(content='{"overview":"内容"}')
            )
        ]
    )

    def create(**_):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise request_error
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


def test_deepseek_does_not_retry_permanent_sdk_error() -> None:
    summarizer = DeepSeekSummarizer("key", max_retries=3)
    calls = 0
    permanent_error = _status_error(BadRequestError, 400)

    def create(**_):
        nonlocal calls
        calls += 1
        raise permanent_error

    summarizer._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    with pytest.raises(BadRequestError):
        summarizer._call_api("prompt")
    assert calls == 1


def test_summary_rejects_unavailable_summarizer(sample_article) -> None:
    summarizer = SimpleNamespace(is_available=lambda: False)
    use_case = SummarizeArticleUseCase(summarizer, SummarySourceRenderer())
    with pytest.raises(SummarizerNotAvailableError):
        use_case.execute(sample_article)


def test_summary_generation_conditions_track_projection_version(monkeypatch) -> None:
    use_case = SummarizeArticleUseCase(SimpleNamespace(), SummarySourceRenderer())
    initial = use_case.generation_conditions(1_200)

    monkeypatch.setattr(projection_module, "PROJECTION_FORMAT_VERSION", 2)

    changed = use_case.generation_conditions(1_200)
    assert initial["projection_version"] == 1
    assert changed["projection_version"] == 2
    assert initial != changed


def test_summary_rejects_overlong_article_without_calling_model(sample_article) -> None:
    summarizer = SimpleNamespace(
        is_available=lambda: True, summarize=lambda **_: pytest.fail("must not call model")
    )
    article = sample_article
    object.__setattr__(article, "content", ArticleContent.from_text("x" * 2_000))
    use_case = SummarizeArticleUseCase(summarizer, SummarySourceRenderer(), max_input_chars=1_000)
    with pytest.raises(SummarizerTokenLimitError, match="文章过长"):
        use_case.execute(article)


def test_summary_failure_logs_error_type_not_model_body(
    sample_article, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "SECRET_MODEL_BODY_SHOULD_NOT_APPEAR"
    captured: list[str] = []

    def _capture(message: object, *args: object, **kwargs: object) -> None:
        captured.append(str(message))
        captured.extend(str(item) for item in args)
        captured.extend(str(value) for value in kwargs.values())

    monkeypatch.setattr(
        "wechat_article_reader.application.use_cases.summarize_article.logger.error",
        _capture,
    )
    monkeypatch.setattr(
        "wechat_article_reader.application.use_cases.summarize_article.logger.warning",
        _capture,
    )

    class _Fail:
        def is_available(self) -> bool:
            return True

        def summarize(self, **_: object):
            raise RuntimeError(secret)

    with pytest.raises(UseCaseError) as err:
        SummarizeArticleUseCase(_Fail(), SummarySourceRenderer()).execute(sample_article)

    joined = " ".join(captured)
    message = str(err.value)
    assert secret not in message
    assert "摘要生成失败" in message
    assert secret not in joined
    assert "error_type={}" in joined
    assert "RuntimeError" in joined
