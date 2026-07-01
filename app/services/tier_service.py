from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AnalyticsCache, Post, PostMetric, Source


POST_TIER_THRESHOLDS = (
    (100_000, "viral"),
    (30_000, "high"),
    (8_000, "medium"),
    (1_000, "low"),
)

METRIC_UPDATE_INTERVAL_MINUTES = {
    "bootstrap": 5,
    "viral": 15,
    "high": 30,
    "medium": 60,
    "low": 180,
    "very_low": 720,
}

SOURCE_TIER_THRESHOLDS = (
    (85, 5),
    (65, 4),
    (40, 3),
    (20, 2),
)

ROLLING_WINDOW_DAYS = {
    "hashtag": 3,
    "keyword": 3,
    "user": 7,
}

BASE_INTERVAL_MINUTES = {
    "hashtag": 45,
    "keyword": 60,
    "user": 90,
}

TIER_MULTIPLIER = {
    5: 0.75,
    4: 1,
    3: 1.5,
    2: 2.5,
    1: 4,
}

MIN_INTERVAL_MINUTES = {
    "hashtag": 15,
    "keyword": 15,
    "user": 15,
}

MAX_INTERVAL_MINUTES = {
    "hashtag": 180,
    "keyword": 240,
    "user": 360,
}


def _day_start(value: datetime) -> datetime:
    return datetime(value.year, value.month, value.day)


def _value(value: int | float | None) -> int | float:
    return value or 0


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def calculate_post_metric_score(
    views: int | None = 0,
    likes: int | None = 0,
    comments: int | None = 0,
    shares: int | None = 0,
    bookmarks: int | None = 0,
) -> float:
    score = (
        _value(views) * 0.2
        + _value(likes) * 5
        + _value(comments) * 12
        + _value(shares) * 15
        + _value(bookmarks) * 10
    )
    return round(float(score), 2)


def calculate_post_metric_tier(score: float) -> str:
    for threshold, tier in POST_TIER_THRESHOLDS:
        if score >= threshold:
            return tier
    return "very_low"


def metric_tier_from_metric(metric: PostMetric) -> str:
    score = calculate_post_metric_score(
        views=metric.views_count,
        likes=metric.likes_count,
        comments=metric.comments_count,
        shares=metric.shares_count,
        bookmarks=metric.bookmarks_count,
    )
    return calculate_post_metric_tier(score)


def next_metric_update_at(recorded_at: datetime, metric_tier: str) -> datetime:
    minutes = METRIC_UPDATE_INTERVAL_MINUTES.get(
        metric_tier,
        METRIC_UPDATE_INTERVAL_MINUTES["medium"],
    )
    return recorded_at + timedelta(minutes=minutes)


def calculate_source_score(cache_rows: list[AnalyticsCache]) -> float:
    total_posts = sum(int(_value(row.total_posts)) for row in cache_rows)
    total_likes = sum(int(_value(row.total_likes)) for row in cache_rows)
    total_shares = sum(int(_value(row.total_shares)) for row in cache_rows)
    total_comments = sum(int(_value(row.total_comments)) for row in cache_rows)
    total_views = sum(int(_value(row.total_views)) for row in cache_rows)
    total_bookmarks = sum(int(_value(row.total_bookmarks)) for row in cache_rows)

    if total_posts <= 0:
        return 0

    total_schedule_score = calculate_post_metric_score(
        views=total_views,
        likes=total_likes,
        comments=total_comments,
        shares=total_shares,
        bookmarks=total_bookmarks,
    )
    avg_schedule_score = total_schedule_score / total_posts
    quality_score = min((avg_schedule_score / 30_000) * 100, 100)
    volume_score = min((total_posts / 50) * 100, 100)

    if total_views > 0:
        engagement_rate = (
            total_likes + total_comments * 2 + total_shares * 3 + total_bookmarks * 2
        ) / total_views
        engagement_score = min((engagement_rate / 0.10) * 100, 100)
    else:
        engagement_score = 0

    growth_values = [float(row.growth_rate) for row in cache_rows if row.growth_rate is not None]
    growth_rate = sum(growth_values) / len(growth_values) if growth_values else 0
    growth_score = _clamp(((growth_rate + 50) / 150) * 100, 0, 100)

    score = (
        quality_score * 0.45
        + volume_score * 0.20
        + engagement_score * 0.20
        + growth_score * 0.15
    )
    return round(score, 2)


def calculate_source_schedule_tier(source_score: float) -> int:
    for threshold, tier in SOURCE_TIER_THRESHOLDS:
        if source_score >= threshold:
            return tier
    return 1


def get_load_multiplier(total_active_sources: int) -> float:
    if total_active_sources <= 50:
        return 1
    if total_active_sources <= 200:
        return 1.5
    if total_active_sources <= 500:
        return 2
    return 3


def calculate_next_scrape_interval(
    source_type: str,
    schedule_tier: int | None,
    total_active_sources: int,
    schedule_override_minutes: int | None = None,
) -> timedelta:
    if schedule_override_minutes is not None:
        return timedelta(minutes=schedule_override_minutes)

    tier = schedule_tier or 1
    base = BASE_INTERVAL_MINUTES.get(source_type, 120)
    tier_multiplier = TIER_MULTIPLIER.get(tier, 1)
    load_multiplier = get_load_multiplier(total_active_sources)
    minutes = int(base * tier_multiplier * load_multiplier)

    min_minutes = MIN_INTERVAL_MINUTES.get(source_type, 30)
    max_minutes = MAX_INTERVAL_MINUTES.get(source_type, 360)
    minutes = max(min_minutes, min(max_minutes, minutes))
    return timedelta(minutes=minutes)


def _latest_metric_for_post(db: Session, post_id: int) -> PostMetric | None:
    return (
        db.query(PostMetric)
        .filter(PostMetric.post_id == post_id)
        .order_by(PostMetric.recorded_at.desc(), PostMetric.id.desc())
        .first()
    )


def upsert_source_analytics_cache(
    db: Session,
    source: Source,
    now: datetime,
) -> AnalyticsCache:
    date = _day_start(now)
    next_date = date + timedelta(days=1)
    posts = (
        db.query(Post)
        .filter(Post.source_id == source.id)
        .filter(Post.posted_at >= date)
        .filter(Post.posted_at < next_date)
        .all()
    )

    total_likes = 0
    total_shares = 0
    total_comments = 0
    total_views = 0
    total_bookmarks = 0
    top_post_id = None
    top_score = -1.0

    for post in posts:
        metric = _latest_metric_for_post(db, post.id)
        if metric is None:
            continue
        total_likes += int(_value(metric.likes_count))
        total_shares += int(_value(metric.shares_count))
        total_comments += int(_value(metric.comments_count))
        total_views += int(_value(metric.views_count))
        total_bookmarks += int(_value(metric.bookmarks_count))
        score = calculate_post_metric_score(
            views=metric.views_count,
            likes=metric.likes_count,
            comments=metric.comments_count,
            shares=metric.shares_count,
            bookmarks=metric.bookmarks_count,
        )
        if score > top_score:
            top_score = score
            top_post_id = post.tiktok_video_id

    previous_cache = (
        db.query(AnalyticsCache)
        .filter(AnalyticsCache.source_id == source.id)
        .filter(AnalyticsCache.date == date - timedelta(days=1))
        .first()
    )
    previous_views = int(_value(previous_cache.total_views)) if previous_cache else 0
    growth_rate = ((total_views - previous_views) / previous_views * 100) if previous_views > 0 else 0

    cache = (
        db.query(AnalyticsCache)
        .filter(AnalyticsCache.source_id == source.id)
        .filter(AnalyticsCache.date == date)
        .first()
    )
    if cache is None:
        cache = AnalyticsCache(source_id=source.id, date=date)
        db.add(cache)

    cache.total_posts = len(posts)
    cache.total_likes = total_likes
    cache.total_shares = total_shares
    cache.total_comments = total_comments
    cache.total_views = total_views
    cache.total_bookmarks = total_bookmarks
    cache.avg_likes_per_post = total_likes / len(posts) if posts else 0
    cache.top_post_id = top_post_id
    cache.growth_rate = round(growth_rate, 2)
    cache.cached_at = now
    return cache


def rolling_cache_rows(db: Session, source: Source, now: datetime) -> list[AnalyticsCache]:
    window_days = ROLLING_WINDOW_DAYS.get(source.source_type, 3)
    end_date = _day_start(now)
    start_date = end_date - timedelta(days=window_days - 1)
    return (
        db.query(AnalyticsCache)
        .filter(AnalyticsCache.source_id == source.id)
        .filter(AnalyticsCache.date >= start_date)
        .filter(AnalyticsCache.date <= end_date)
        .order_by(AnalyticsCache.date.desc())
        .all()
    )


def refresh_source_schedule(db: Session, source: Source, now: datetime) -> float:
    upsert_source_analytics_cache(db, source, now)
    db.flush()

    cache_rows = rolling_cache_rows(db, source, now)
    source_score = calculate_source_score(cache_rows)
    source.schedule_tier = calculate_source_schedule_tier(source_score)

    total_active_sources = db.query(Source).filter(Source.is_active.is_(True)).count()
    interval = calculate_next_scrape_interval(
        source.source_type,
        source.schedule_tier,
        total_active_sources,
        source.schedule_override_minutes,
    )
    source.next_scrape = now + interval
    return source_score
