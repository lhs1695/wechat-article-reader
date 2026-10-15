"""来源实体"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum

from ..value_objects import ContentNote

WECHAT_PUBLISH_TIME_TIMEZONE = "Asia/Shanghai"
# WeChat publication times use UTC+08:00; a fixed offset avoids a system tzdata dependency.
WECHAT_PUBLISH_TIME_TZ = timezone(timedelta(hours=8), WECHAT_PUBLISH_TIME_TIMEZONE)


class SourceType(StrEnum):
    """来源类型"""

    WECHAT = "wechat"  # 微信公众号


@dataclass(frozen=True)
class ArticleSource:
    """
    文章来源值对象

    记录文章的来源信息，包括来源类型、平台名称等。
    """

    type: SourceType = SourceType.WECHAT
    platform: str = "微信公众号"
    account_id: str | None = None  # 账号ID
    account_name: str | None = None  # 账号名称

    # 抓取信息
    scraped_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    scraper_name: str = "unknown"
    content_notes: tuple[ContentNote, ...] = ()
    publish_time_timezone: str | None = None

    @classmethod
    def wechat(
        cls,
        account_name: str,
        account_id: str | None = None,
        content_notes: tuple[ContentNote, ...] = (),
        publish_time_timezone: str | None = None,
    ) -> ArticleSource:
        """创建微信公众号来源"""
        return cls(
            type=SourceType.WECHAT,
            platform="微信公众号",
            account_id=account_id,
            account_name=account_name,
            scraper_name="wechat_scraper",
            content_notes=content_notes,
            publish_time_timezone=publish_time_timezone,
        )
