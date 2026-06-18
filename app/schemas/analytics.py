from datetime import datetime

from app.schemas.base import ORMBase


class AnalyticsCacheRead(ORMBase):
    id: int
    source_id: int
    date: datetime
    total_posts: int | None = None
    total_likes: int | None = None
    total_shares: int | None = None
    total_comments: int | None = None
    total_views: int | None = None
    total_bookmarks: int | None = None
    avg_likes_per_post: float | None = None
    top_post_id: str | None = None
    growth_rate: float | None = None
    cached_at: datetime | None = None
