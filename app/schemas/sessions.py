from datetime import datetime

from pydantic import Field

from app.schemas.base import ORMBase


class TikTokSessionCreate(ORMBase):
    ms_token: str = Field(min_length=1)
    is_active: bool = True
    is_valid: bool = True
    expires_at: datetime | None = None
    deactivate_existing: bool = True


class TikTokSessionUpdate(ORMBase):
    is_active: bool | None = None
    is_valid: bool | None = None
    expires_at: datetime | None = None


class TikTokSessionRead(ORMBase):
    id: int
    masked_ms_token: str
    is_active: bool
    is_valid: bool
    last_verified: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None
