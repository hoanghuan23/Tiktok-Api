from datetime import datetime

from app.schemas.base import ORMBase
from app.schemas.metrics import PostMetricRead


class PostRead(ORMBase):
    id: int
    source_id: int
    tiktok_video_id: str
    tiktok_url: str
    description: str | None = None
    duration_seconds: int | None = None
    cover_url: str | None = None
    posted_at: datetime
    created_at: datetime | None = None
    is_tracked: bool | None = None
    tracking_until: datetime | None = None
    is_deleted: bool | None = None
    last_metric_update: datetime | None = None
    metric_tier: str
    next_metric_update: datetime | None = None
    last_engagement_velocity: float | None = None
    cold_check_count: int
    metric_scan_miss_count: int


class PostDetail(ORMBase):
    post: PostRead
    latest_metric: PostMetricRead | None = None
