import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.models import Post, PostMetric, Source
from app.services import scraper_service
from app.services.scraper_service import crawl_source
from app.services.tiktok_client import TikTokClient


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return session_local()


def test_crawl_user_source_only_requests_videos_since_last_24_hours(monkeypatch):
    calls = []

    async def fake_get_user_videos(self, username, max_count, since=None):
        calls.append((username, max_count, since))
        return []

    monkeypatch.setattr(TikTokClient, "get_user_videos", fake_get_user_videos)
    db = _session()
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.commit()
    db.refresh(source)

    before_cutoff = scraper_service._now() - timedelta(hours=24)
    job = asyncio.run(crawl_source(db, source, max_count=30))
    after_cutoff = scraper_service._now() - timedelta(hours=24)

    assert job.status == "done"
    assert len(calls) == 1
    username, max_count, since = calls[0]
    assert username == "vtv24news"
    assert max_count == 30
    assert before_cutoff <= since <= after_cutoff


def test_crawl_hashtag_source_does_not_use_user_video_cutoff(monkeypatch):
    async def fail_if_user_videos_called(self, username, max_count, since=None):
        raise AssertionError("user cutoff should only apply to user sources")

    hashtag_calls = []

    async def fake_get_hashtag_videos(self, hashtag_name, max_count):
        hashtag_calls.append((hashtag_name, max_count))
        return []

    monkeypatch.setattr(TikTokClient, "get_user_videos", fail_if_user_videos_called)
    monkeypatch.setattr(TikTokClient, "get_hashtag_videos", fake_get_hashtag_videos)
    db = _session()
    source = Source(source_type="hashtag", identifier="python", is_active=True)
    db.add(source)
    db.commit()
    db.refresh(source)

    job = asyncio.run(crawl_source(db, source, max_count=30))

    assert job.status == "done"
    assert hashtag_calls == [("python", 30)]


def test_crawl_source_saves_posted_at_from_video_as_dict_create_time(monkeypatch):
    expected_posted_at = datetime(2026, 1, 2, 11, 0, 0)
    create_time = int(expected_posted_at.replace(tzinfo=timezone.utc).timestamp())
    video = SimpleNamespace(
        id="video-1",
        as_dict={
            "id": "video-1",
            "createTime": create_time,
            "author": {"uniqueId": "vtv24news"},
            "video": {"duration": 30, "cover": "https://example.com/cover.jpg"},
        },
    )

    async def fake_get_user_videos(self, username, max_count, since=None):
        return [video]

    monkeypatch.setattr(TikTokClient, "get_user_videos", fake_get_user_videos)
    db = _session()
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.commit()
    db.refresh(source)

    job = asyncio.run(crawl_source(db, source, max_count=30))
    post = db.query(Post).filter(Post.tiktok_video_id == "video-1").one()

    assert job.status == "done"
    assert post.posted_at == expected_posted_at
    assert post.tiktok_url == "https://www.tiktok.com/@vtv24news/video/video-1"


def test_crawl_source_creates_initial_post_metric_from_video_stats(monkeypatch):
    video = SimpleNamespace(
        id="video-1",
        as_dict={
            "id": "video-1",
            "createTime": 1767351600,
            "author": {"uniqueId": "vtv24news"},
            "statsV2": {
                "diggCount": "1187",
                "shareCount": "49",
                "commentCount": "35",
                "playCount": "45200",
                "collectCount": "242",
            },
        },
    )

    async def fake_get_user_videos(self, username, max_count, since=None):
        return [video]

    monkeypatch.setattr(TikTokClient, "get_user_videos", fake_get_user_videos)
    db = _session()
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.commit()
    db.refresh(source)

    job = asyncio.run(crawl_source(db, source, max_count=30))
    post = db.query(Post).filter(Post.tiktok_video_id == "video-1").one()
    metric = db.query(PostMetric).filter(PostMetric.post_id == post.id).one()

    assert job.status == "done"
    assert metric.likes_count == 1187
    assert metric.shares_count == 49
    assert metric.comments_count == 35
    assert metric.views_count == 45200
    assert metric.bookmarks_count == 242
    assert metric.job_id == job.id
    assert post.last_metric_update == metric.recorded_at
