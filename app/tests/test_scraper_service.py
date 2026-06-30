import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.models import Hashtag, PipelineLog, Post, PostHashtag, PostMetric, Source, TaskLog
from app.services import scraper_service
from app.services.scraper_service import crawl_source
from app.services.tiktok_client import TikTokClient


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return session_local()


def test_extract_hashtags_from_description():
    assert scraper_service._extract_hashtags("#theanh28 #hanoinews #tiktoknews") == [
        "theanh28",
        "hanoinews",
        "tiktoknews",
    ]


def test_extract_hashtags_deduplicates_case_variants():
    assert scraper_service._extract_hashtags("#HaNoiNews update #hanoinews #TIN_TUC") == [
        "hanoinews",
        "tin_tuc",
    ]


def test_extract_hashtags_ignores_empty_description():
    assert scraper_service._extract_hashtags(None) == []
    assert scraper_service._extract_hashtags("") == []


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


def test_crawl_user_source_uses_max_days_old_for_cutoff(monkeypatch):
    calls = []

    async def fake_get_user_videos(self, username, max_count, since=None):
        calls.append((username, max_count, since))
        return []

    monkeypatch.setattr(TikTokClient, "get_user_videos", fake_get_user_videos)
    db = _session()
    source = Source(source_type="user", identifier="vtv24news", max_days_old=2, is_active=True)
    db.add(source)
    db.commit()
    db.refresh(source)

    before_cutoff = scraper_service._now() - timedelta(days=2)
    job = asyncio.run(crawl_source(db, source, max_count=30))
    after_cutoff = scraper_service._now() - timedelta(days=2)

    assert job.status == "done"
    _, _, since = calls[0]
    assert before_cutoff <= since <= after_cutoff


def test_crawl_user_source_treats_zero_max_days_old_as_24_hours(monkeypatch):
    calls = []

    async def fake_get_user_videos(self, username, max_count, since=None):
        calls.append((username, max_count, since))
        return []

    monkeypatch.setattr(TikTokClient, "get_user_videos", fake_get_user_videos)
    db = _session()
    source = Source(source_type="user", identifier="vtv24news", max_days_old=0, is_active=True)
    db.add(source)
    db.commit()
    db.refresh(source)

    before_cutoff = scraper_service._now() - timedelta(hours=24)
    job = asyncio.run(crawl_source(db, source, max_count=30))
    after_cutoff = scraper_service._now() - timedelta(hours=24)

    assert job.status == "done"
    _, _, since = calls[0]
    assert before_cutoff <= since <= after_cutoff


def test_crawl_user_source_uses_latest_posted_at_when_it_is_newer_than_24h(monkeypatch):
    calls = []

    async def fake_get_user_videos(self, username, max_count, since=None):
        calls.append((username, max_count, since))
        return []

    monkeypatch.setattr(TikTokClient, "get_user_videos", fake_get_user_videos)
    db = _session()
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.flush()
    latest_posted_at = scraper_service._now() - timedelta(hours=2)
    db.add(
        Post(
            source_id=source.id,
            tiktok_video_id="existing-video",
            tiktok_url="https://www.tiktok.com/@vtv24news/video/existing-video",
            posted_at=latest_posted_at,
        )
    )
    db.commit()
    db.refresh(source)

    job = asyncio.run(crawl_source(db, source, max_count=30))

    assert job.status == "done"
    assert len(calls) == 1
    _, _, since = calls[0]
    assert since == latest_posted_at


def test_crawl_source_writes_task_log_summary(monkeypatch):
    async def fake_get_user_videos(self, username, max_count, since=None):
        return []

    monkeypatch.setattr(TikTokClient, "get_user_videos", fake_get_user_videos)
    db = _session()
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.commit()
    db.refresh(source)

    job = asyncio.run(crawl_source(db, source, max_count=30))
    task_log = db.query(TaskLog).one()

    assert job.status == "done"
    assert source.last_scraped == job.finished_at
    assert source.next_scrape is not None
    assert source.schedule_tier == 1
    assert task_log.task_name == "scrape_posts"
    assert task_log.status == "done"
    assert task_log.started_at == job.started_at
    assert task_log.completed_at == job.finished_at
    assert task_log.items_processed == 0
    assert task_log.errors_count == 0
    assert task_log.error_message is None
    assert db.query(PipelineLog).count() == 0


def test_crawl_source_writes_failed_task_log_summary():
    db = _session()
    source = Source(source_type="sound", identifier="sound-1", is_active=True)
    db.add(source)
    db.commit()
    db.refresh(source)

    job = asyncio.run(crawl_source(db, source, max_count=30))
    task_log = db.query(TaskLog).one()

    assert job.status == "failed"
    assert task_log.task_name == "scrape_posts"
    assert task_log.status == "failed"
    assert task_log.errors_count == 1
    assert "Chua ho tro crawl source_type=sound" in task_log.error_message
    pipeline_log = db.query(PipelineLog).one()
    assert pipeline_log.log_level == "ERROR"
    assert pipeline_log.error_type == "ValueError"
    assert "Chua ho tro crawl source_type=sound" in pipeline_log.error_details


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


def test_crawl_keyword_source_uses_keyword_search_like_hashtag(monkeypatch):
    async def fail_if_user_videos_called(self, username, max_count, since=None):
        raise AssertionError("user cutoff should only apply to user sources")

    keyword_calls = []
    video = SimpleNamespace(
        id="video-kw-1",
        as_dict={
            "id": "video-kw-1",
            "desc": "Ket qua tim kiem #keyword",
            "createTime": 1767351600,
            "author": {"uniqueId": "search_author"},
            "statsV2": {
                "diggCount": "10",
                "shareCount": "2",
                "commentCount": "1",
                "playCount": "100",
                "collectCount": "3",
            },
        },
    )

    async def fake_get_keyword_videos(self, keyword, max_count):
        keyword_calls.append((keyword, max_count))
        return [video]

    monkeypatch.setattr(TikTokClient, "get_user_videos", fail_if_user_videos_called)
    monkeypatch.setattr(TikTokClient, "get_keyword_videos", fake_get_keyword_videos)
    db = _session()
    source = Source(source_type="keyword", identifier="doreamon", is_active=True)
    db.add(source)
    db.commit()
    db.refresh(source)

    job = asyncio.run(crawl_source(db, source, max_count=30))
    post = db.query(Post).filter(Post.tiktok_video_id == "video-kw-1").one()
    metric = db.query(PostMetric).filter(PostMetric.post_id == post.id).one()

    assert job.status == "done"
    assert job.posts_found == 1
    assert job.posts_new == 1
    assert keyword_calls == [("doreamon", 30)]
    assert post.tiktok_url == "https://www.tiktok.com/@search_author/video/video-kw-1"
    assert [hashtag.tag for hashtag in post.hashtags] == ["keyword"]
    assert metric.views_count == 100
    assert post.metric_tier == "very_low"


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
    assert post.metric_tier == "medium"
    assert post.next_metric_update == scraper_service.next_metric_update_at(metric.recorded_at)


def test_crawl_source_creates_hashtags_for_new_posts(monkeypatch):
    videos = [
        SimpleNamespace(
            id="video-1",
            as_dict={
                "id": "video-1",
                "desc": "Tin nong #theanh28 #hanoinews #TikTokNews #hanoinews",
                "createTime": 1767351600,
                "author": {"uniqueId": "vtv24news"},
            },
        ),
        SimpleNamespace(
            id="video-2",
            as_dict={
                "id": "video-2",
                "desc": "Ban tin moi #hanoinews #xahoi",
                "createTime": 1767351660,
                "author": {"uniqueId": "vtv24news"},
            },
        ),
    ]

    async def fake_get_user_videos(self, username, max_count, since=None):
        return videos

    monkeypatch.setattr(TikTokClient, "get_user_videos", fake_get_user_videos)
    db = _session()
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.commit()
    db.refresh(source)

    job = asyncio.run(crawl_source(db, source, max_count=30))
    post_1 = db.query(Post).filter(Post.tiktok_video_id == "video-1").one()
    post_2 = db.query(Post).filter(Post.tiktok_video_id == "video-2").one()

    assert job.status == "done"
    assert job.posts_new == 2
    assert [hashtag.tag for hashtag in post_1.hashtags] == ["theanh28", "hanoinews", "tiktoknews"]
    assert [hashtag.tag for hashtag in post_2.hashtags] == ["hanoinews", "xahoi"]
    assert db.query(Hashtag).filter(Hashtag.tag == "hanoinews").count() == 1
    assert db.query(Hashtag).count() == 4
    assert db.query(PostHashtag).count() == 5


def test_crawl_source_skips_duplicate_post_without_adding_hashtags(monkeypatch):
    video = SimpleNamespace(
        id="video-1",
        as_dict={
            "id": "video-1",
            "desc": "Duplicate should be skipped #newtag",
            "createTime": 1767351600,
            "author": {"uniqueId": "vtv24news"},
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

    existing_post = Post(
        source_id=source.id,
        tiktok_video_id="video-1",
        tiktok_url="https://www.tiktok.com/@vtv24news/video/video-1",
        posted_at=datetime(2026, 1, 2, 11, 0, 0),
    )
    db.add(existing_post)
    db.commit()

    job = asyncio.run(crawl_source(db, source, max_count=30))

    assert job.status == "done"
    assert job.posts_new == 0
    assert db.query(Hashtag).count() == 0
    assert db.query(PostHashtag).count() == 0


def test_crawl_user_source_stops_when_video_reaches_latest_posted_at(monkeypatch):
    latest_posted_at = datetime(2026, 1, 2, 11, 0, 0)

    def video(video_id, posted_at):
        return SimpleNamespace(
            id=video_id,
            as_dict={
                "id": video_id,
                "desc": f"Video {video_id}",
                "createTime": int(posted_at.replace(tzinfo=timezone.utc).timestamp()),
                "author": {"uniqueId": "vtv24news"},
            },
        )

    videos = [
        video("video-new", datetime(2026, 1, 2, 12, 0, 0)),
        video("video-at-latest", latest_posted_at),
        video("video-old", datetime(2026, 1, 2, 10, 0, 0)),
    ]

    async def fake_get_user_videos(self, username, max_count, since=None):
        return videos

    monkeypatch.setattr(TikTokClient, "get_user_videos", fake_get_user_videos)
    db = _session()
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.flush()
    db.add(
        Post(
            source_id=source.id,
            tiktok_video_id="existing-video",
            tiktok_url="https://www.tiktok.com/@vtv24news/video/existing-video",
            posted_at=latest_posted_at,
        )
    )
    db.commit()
    db.refresh(source)

    job = asyncio.run(crawl_source(db, source, max_count=30))

    assert job.status == "done"
    assert job.posts_found == 1
    assert job.items_total == 1
    assert job.posts_new == 1
    assert db.query(Post).filter(Post.tiktok_video_id == "video-new").count() == 1
    assert db.query(Post).filter(Post.tiktok_video_id == "video-at-latest").count() == 0
    assert db.query(Post).filter(Post.tiktok_video_id == "video-old").count() == 0
