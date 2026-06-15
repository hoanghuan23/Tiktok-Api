from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.base import ORMBase


SourceType = Literal["user", "hashtag", "sound", "keyword"]


class SourceBase(ORMBase):
    source_type: SourceType
    identifier: str = Field(min_length=1, max_length=100)
    display_name: str | None = None
    max_days_old: int | None = None


class SourceCreate(SourceBase):
    include_comments: bool = False


class SourceUpdate(ORMBase):
    is_active: bool | None = None
    display_name: str | None = None
    include_comments: bool | None = None
    max_days_old: int | None = None
    schedule_override_minutes: int | None = None
    is_accessible: bool | None = None


class SourceRead(SourceBase):
    id: int
    tiktok_url: str | None = None
    follower_count: int | None = None
    is_active: bool | None = None
    is_accessible: bool | None = None
    created_at: datetime | None = None
    last_scraped: datetime | None = None
    next_scrape: datetime | None = None
    schedule_tier: int | None = None
    schedule_override_minutes: int | None = None
