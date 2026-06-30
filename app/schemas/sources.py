from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.base import ORMBase


SourceType = Literal["user", "hashtag", "sound", "keyword"]


class SourceBase(ORMBase):
    source_type: SourceType
    display_name: str | None = None
    max_days_old: int | None = Field(default=None, ge=0)


class SourceCreate(SourceBase):
    identifier: str | None = Field(default=None, min_length=1, max_length=100)
    tiktok_url: str | None = Field(default=None, min_length=1, max_length=255)
    include_comments: bool = False

    @model_validator(mode="after")
    def validate_source_identity(self) -> "SourceCreate":
        if self.source_type == "user":
            if not self.tiktok_url:
                raise ValueError("tiktok_url is required for user sources")
            return self
        if not self.identifier:
            raise ValueError("identifier is required for non-user sources")
        return self


class SourceUpdate(ORMBase):
    is_active: bool | None = None
    display_name: str | None = None
    include_comments: bool | None = None
    max_days_old: int | None = Field(default=None, ge=0)
    schedule_override_minutes: int | None = None
    is_accessible: bool | None = None


class SourceRead(SourceBase):
    id: int
    identifier: str
    tiktok_url: str | None = None
    is_active: bool | None = None
    is_accessible: bool | None = None
    created_at: datetime | None = None
    last_scraped: datetime | None = None
    next_scrape: datetime | None = None
    schedule_tier: int | None = None
    schedule_override_minutes: int | None = None
