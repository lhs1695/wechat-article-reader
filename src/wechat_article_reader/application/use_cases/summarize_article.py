"""生成摘要用例"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from threading import Event
from typing import TYPE_CHECKING

from loguru import logger

from ...domain.entities import Article, Summary, SummaryEvidence
from ...domain.value_objects import ArticleContent
from ...shared.constants import DEFAULT_SUMMARY_MAX_LENGTH
from ...shared.exceptions import (
    OperationCancelledError,
    OperationDeadlineExceededError,
    SummarizerNotAvailableError,
    SummarizerTokenLimitError,
    UseCaseError,
)

if TYPE_CHECKING:
    from ..ports.outbound import SummarizerPort, SummarySourceRendererPort


class SummarizeArticleUseCase:
    """
    生成摘要用例

    负责协调摘要器来生成文章摘要。
    """

    def __init__(
        self,
        summarizer: SummarizerPort | None,
        summary_source_renderer: SummarySourceRendererPort,
        max_input_chars: int = 50_000,
    ):
        """
        Args:
            summarizer: 单一 DeepSeek 摘要器
        """
        self._summarizer = summarizer
        self._summary_source_renderer = summary_source_renderer
        self._max_input_chars = max_input_chars

    def generation_conditions(self, max_length: int) -> dict[str, object]:
        profile = getattr(self._summarizer, "generation_profile", {})
        return {
            **(profile if isinstance(profile, dict) else {}),
            "max_length": max_length,
            "prompt_version": "structured-v3",
            "projection_version": getattr(self._summary_source_renderer, "projection_version", 1),
        }

    def execute(
        self,
        article: Article,
        max_length: int = DEFAULT_SUMMARY_MAX_LENGTH,
        *,
        cancel_event: Event | None = None,
    ) -> Summary:
        """
        执行生成摘要用例

        Args:
            article: 文章实体
            max_length: 最大字数

        Returns:
            生成的摘要

        Raises:
            SummarizerNotAvailableError: DeepSeek 未配置或不可用
            SummarizerTokenLimitError: 摘要输入超过单次上限
            UseCaseError: 摘要生成失败
        """
        if article.content is None:
            raise UseCaseError("文章内容为空，无法生成摘要")

        summarizer = self._summarizer
        if summarizer is None or not summarizer.is_available():
            raise SummarizerNotAvailableError("DeepSeek 摘要器不可用")

        summary_content = ArticleContent.from_text(self._summary_source_renderer.render(article))
        if len(summary_content.text) > self._max_input_chars:
            raise SummarizerTokenLimitError("文章过长，请使用分页 Markdown 阅读")

        # 生成摘要
        logger.info("使用 DeepSeek 生成摘要")

        try:
            summary = summarizer.summarize(
                content=summary_content,
                max_length=max_length,
                cancel_event=cancel_event,
            )

            logger.info("摘要生成成功")
            generation = self.generation_conditions(max_length)
            generation["input_char_count"] = len(summary_content.text)
            evidence = self._verify_evidence(
                summary.evidence, summary.key_points, summary_content.text
            )
            return replace(
                summary,
                input_hash=hashlib.sha256(summary_content.text.encode("utf-8")).hexdigest(),
                generation=generation,
                evidence=evidence,
            )

        except OperationCancelledError:
            raise
        except OperationDeadlineExceededError:
            raise
        except (SummarizerNotAvailableError, SummarizerTokenLimitError):
            raise
        except Exception as exc:
            logger.error("摘要生成失败 error_type={}", type(exc).__name__)
            raise UseCaseError("摘要生成失败") from exc

    @staticmethod
    def _verify_evidence(
        candidates: tuple[SummaryEvidence, ...], key_points: tuple[str, ...], source: str
    ) -> tuple[SummaryEvidence, ...]:
        """Keep only exact, contiguous quotations located inside the rendered article body."""
        opening = '<article_source trust="untrusted_web_content">\n'
        closing = "\n</article_source>"
        marker_start = source.find(opening)
        if marker_start < 0:
            return ()
        body_start = marker_start + len(opening)
        body_end = source.find(closing, body_start)
        if body_end < 0:
            return ()
        body = source[body_start:body_end]
        verified: list[SummaryEvidence] = []
        seen_points: set[int] = set()
        for candidate in candidates[:5]:
            index, quote = candidate.key_point_index, candidate.quote
            if (
                type(index) is not int
                or not 0 <= index < len(key_points)
                or index in seen_points
                or not quote
                or len(quote) > 500
            ):
                continue
            local_start = body.find(quote)
            if local_start < 0:
                continue
            input_start = body_start + local_start
            verified.append(SummaryEvidence(index, quote, input_start, input_start + len(quote)))
            seen_points.add(index)
        return tuple(verified)
