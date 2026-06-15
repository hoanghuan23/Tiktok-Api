from datetime import datetime

from app.schemas.base import ORMBase


class PostMetricRead(ORMBase):
    id: int
    post_id: int
    likes_count: int | None = None
    shares_count: int | None = None
    comments_count: int | None = None
    views_count: int | None = None
    recorded_at: datetime | None = None
    job_id: int | None = None
