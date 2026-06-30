from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.models import AnalyticsCache, Post, PostMetric, Source
from app.services.tier_service import (
    calculate_next_scrape_interval,
    calculate_post_metric_score,
    calculate_post_metric_tier,
    calculate_source_schedule_tier,
    calculate_source_score,
    next_metric_update_at,
    refresh_source_schedule,
    upsert_source_analytics_cache,
)


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return session_local()


def test_calculate_post_metric_score_and_tier_thresholds():
    assert calculate_post_metric_score(views=100, likes=10, comments=1, shares=2, bookmarks=3) == 142
    assert calculate_post_metric_tier(100_000) == "viral"
    assert calculate_post_metric_tier(30_000) == "high"
    assert calculate_post_metric_tier(8_000) == "medium"
    assert calculate_post_metric_tier(1_000) == "low"
    assert calculate_post_metric_tier(999.99) == "very_low"


def test_next_metric_update_at_uses_metric_tier_intervals():
    recorded_at = datetime(2026, 1, 2, 12, 0, 0)

    assert next_metric_update_at(recorded_at, "bootstrap") == recorded_at + timedelta(minutes=5)
    assert next_metric_update_at(recorded_at, "viral") == recorded_at + timedelta(minutes=15)
    assert next_metric_update_at(recorded_at, "high") == recorded_at + timedelta(minutes=30)
    assert next_metric_update_at(recorded_at, "medium") == recorded_at + timedelta(minutes=60)
    assert next_metric_update_at(recorded_at, "low") == recorded_at + timedelta(minutes=180)
    assert next_metric_update_at(recorded_at, "very_low") == recorded_at + timedelta(minutes=720)
    assert next_metric_update_at(recorded_at, "unknown") == recorded_at + timedelta(minutes=60)


def test_calculate_source_score_handles_empty_and_zero_views():
    assert calculate_source_score([]) == 0
    score = calculate_source_score(
        [
            AnalyticsCache(
                total_posts=3,
                total_likes=10,
                total_comments=2,
                total_shares=1,
                total_views=0,
                total_bookmarks=4,
                growth_rate=-30,
            )
        ]
    )

    assert score > 0
    assert calculate_source_schedule_tier(score) == 1


def test_calculate_source_score_promotes_strong_sources():
    score = calculate_source_score(
        [
            AnalyticsCache(
                total_posts=50,
                total_likes=100_000,
                total_comments=10_000,
                total_shares=10_000,
                total_views=1_000_000,
                total_bookmarks=10_000,
                growth_rate=100,
            )
        ]
    )

    assert score >= 85
    assert calculate_source_schedule_tier(score) == 5


def test_calculate_next_scrape_interval_uses_tier_load_caps_and_override():
    assert calculate_next_scrape_interval("hashtag", 5, 10) == timedelta(minutes=15)
    assert calculate_next_scrape_interval("hashtag", 1, 600) == timedelta(minutes=180)
    assert calculate_next_scrape_interval("keyword", 3, 100) == timedelta(minutes=52)
    assert calculate_next_scrape_interval("user", 5, 10, 7) == timedelta(minutes=7)


def test_upsert_source_analytics_cache_uses_latest_metric_per_post_and_growth():
    db = _session()
    source = Source(source_type="hashtag", identifier="python", is_active=True)
    db.add(source)
    db.flush()
    post = Post(
        source_id=source.id,
        tiktok_video_id="video-1",
        tiktok_url="https://www.tiktok.com/@author/video/video-1",
        posted_at=datetime(2026, 1, 2, 12, 0, 0),
    )
    db.add(post)
    db.flush()
    db.add_all(
        [
            PostMetric(
                post_id=post.id,
                views_count=100,
                likes_count=10,
                shares_count=1,
                comments_count=2,
                bookmarks_count=3,
                recorded_at=datetime(2026, 1, 2, 12, 5, 0),
            ),
            PostMetric(
                post_id=post.id,
                views_count=250,
                likes_count=20,
                shares_count=3,
                comments_count=4,
                bookmarks_count=5,
                recorded_at=datetime(2026, 1, 2, 12, 10, 0),
            ),
            AnalyticsCache(
                source_id=source.id,
                date=datetime(2026, 1, 1),
                total_views=100,
            ),
        ]
    )
    db.commit()

    cache = upsert_source_analytics_cache(db, source, datetime(2026, 1, 2, 13, 0, 0))

    assert cache.total_posts == 1
    assert cache.total_views == 250
    assert cache.total_likes == 20
    assert cache.total_shares == 3
    assert cache.total_comments == 4
    assert cache.total_bookmarks == 5
    assert cache.top_post_id == "video-1"
    assert cache.growth_rate == 150
    db.close()


def test_refresh_source_schedule_updates_tier_and_next_scrape():
    db = _session()
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.flush()
    post = Post(
        source_id=source.id,
        tiktok_video_id="video-1",
        tiktok_url="https://www.tiktok.com/@vtv24news/video/video-1",
        posted_at=datetime(2026, 1, 2, 12, 0, 0),
    )
    db.add(post)
    db.flush()
    db.add(
        PostMetric(
            post_id=post.id,
            views_count=1_000,
            likes_count=20,
            shares_count=2,
            comments_count=3,
            bookmarks_count=4,
            recorded_at=datetime(2026, 1, 2, 12, 30, 0),
        )
    )
    db.commit()

    now = datetime(2026, 1, 2, 13, 0, 0)
    refresh_source_schedule(db, source, now)

    assert source.schedule_tier == 1
    assert source.next_scrape == now + timedelta(minutes=240)
    db.close()
