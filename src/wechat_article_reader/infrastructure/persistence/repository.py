"""ArticleRepository 的 SQLAlchemy 实现。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ...domain.entities import Article
from ...domain.entities.source import ArticleSource, SourceType
from ...domain.entities.summary import Summary
from ...domain.value_objects import ArticleContent, ArticleURL
from .models import ArticleModel, SummaryModel

TRACKING_QUERY_KEYS = frozenset(
    {"from", "scene", "clicktime", "enterid", "sessionid", "subscene", "ascene"}
)


def normalize_article_url(url: str) -> str:
    """去除不参与文章身份的 fragment 和常见追踪参数。"""
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
    ]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), "")
    )


def content_digest(content: str) -> str:
    normalized = "\n".join(line.strip() for line in content.splitlines() if line.strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SqlAlchemyArticleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, article: Article) -> None:
        normalized_url = normalize_article_url(str(article.url))
        model = self._session.scalar(
            select(ArticleModel).where(ArticleModel.normalized_url == normalized_url)
        )
        if model is None:
            model = ArticleModel(id=str(article.id), normalized_url=normalized_url)
            self._session.add(model)
        elif model.id != str(article.id):
            object.__setattr__(article, "id", UUID(model.id))

        content_hash = content_digest(article.content_text)
        model.url = str(article.url)
        model.title = article.title
        model.author = article.author
        model.account_name = article.account_name
        model.publish_time = article.publish_time
        model.content_html = article.content_html
        model.content_text = article.content_text
        model.images_json = json.dumps(
            list(article.content.images if article.content else ()), ensure_ascii=False
        )
        model.content_hash = content_hash
        model.source_json = self._serialize_source(article.source)
        model.created_at = article.created_at
        model.updated_at = article.updated_at
        self._session.flush()

        if article.summary is not None:
            self._save_summary(model.id, content_hash, article.summary)

    def get(self, article_id: UUID) -> Article | None:
        model = self._session.get(ArticleModel, str(article_id))
        return self._to_domain(model) if model is not None else None

    def get_by_url(self, url: str) -> Article | None:
        normalized_url = normalize_article_url(str(ArticleURL.from_string(url)))
        model = self._session.scalar(
            select(ArticleModel).where(ArticleModel.normalized_url == normalized_url)
        )
        return self._to_domain(model) if model is not None else None

    def list_recent(self, limit: int = 20) -> list[Article]:
        models = self._session.scalars(
            select(ArticleModel).order_by(ArticleModel.updated_at.desc()).limit(max(limit, 0))
        ).all()
        return [self._to_domain(model) for model in models]

    def delete(self, article_id: UUID) -> bool:
        result = self._session.execute(
            delete(ArticleModel).where(ArticleModel.id == str(article_id))
        )
        return bool(getattr(result, "rowcount", 0))

    def count(self) -> int:
        return int(self._session.scalar(select(func.count()).select_from(ArticleModel)) or 0)

    def _save_summary(self, article_id: str, content_hash: str, summary: Summary) -> None:
        model = self._session.scalar(
            select(SummaryModel).where(
                SummaryModel.article_id == article_id,
                SummaryModel.content_hash == content_hash,
            )
        )
        if model is None:
            model = SummaryModel(
                id=str(uuid4()),
                article_id=article_id,
                content_hash=content_hash,
                created_at=summary.created_at,
            )
            self._session.add(model)
        model.overview = summary.overview
        model.key_points_json = json.dumps(list(summary.key_points), ensure_ascii=False)
        model.tags_json = json.dumps(summary.tags, ensure_ascii=False)
        model.one_sentence = summary.one_sentence

    def _to_domain(self, model: ArticleModel) -> Article:
        article = Article(
            id=UUID(model.id),
            url=ArticleURL.from_string(model.url),
            title=model.title,
            author=model.author,
            account_name=model.account_name,
            publish_time=_utc(model.publish_time) if model.publish_time else None,
            content=ArticleContent(
                html=model.content_html,
                text=model.content_text,
                images=tuple(json.loads(model.images_json)),
            ),
            source=self._deserialize_source(model.source_json),
            created_at=_utc(model.created_at),
            updated_at=_utc(model.updated_at),
        )
        summary_model = self._session.scalar(
            select(SummaryModel)
            .where(SummaryModel.article_id == model.id)
            .order_by(SummaryModel.created_at.desc())
            .limit(1)
        )
        if summary_model is not None:
            article.attach_summary(
                Summary(
                    overview=summary_model.overview,
                    key_points=tuple(json.loads(summary_model.key_points_json)),
                    tags=tuple(json.loads(summary_model.tags_json)),
                    one_sentence=summary_model.one_sentence,
                    created_at=_utc(summary_model.created_at),
                )
            )
            object.__setattr__(article, "updated_at", _utc(model.updated_at))
        return article

    @staticmethod
    def _serialize_source(source: ArticleSource | None) -> str | None:
        if source is None:
            return None
        data = asdict(source)
        data["type"] = source.type.value
        data["scraped_at"] = source.scraped_at.isoformat()
        return json.dumps(data, ensure_ascii=False)

    @staticmethod
    def _deserialize_source(payload: str | None) -> ArticleSource | None:
        if not payload:
            return None
        data = json.loads(payload)
        data["type"] = SourceType(data["type"])
        data["scraped_at"] = datetime.fromisoformat(data["scraped_at"])
        return ArticleSource(**data)
