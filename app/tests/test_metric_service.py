import asyncio
import json
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.models import PipelineLog, Post, PostMetric, Source, TaskLog, TikTokSession
from app.services import gallery_dl_tiktok
from app.services import metric_service
from app.services.metric_service import extract_metrics_from_html, update_post_metric, update_source_metrics


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return session_local()


def _active_tiktok_session(db):
    session = TikTokSession(
        sessionid="sessionid",
        tt_csrf_token="csrf",
        ms_token="ms",
        is_active=True,
        is_valid=True,
        expires_at=datetime(2026, 1, 3, 12, 0, 0),
    )
    db.add(session)
    db.flush()
    return session


def _source_with_posts(db, now, count=3):
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.flush()
    posts = []
    for index in range(count):
        post = Post(
            source_id=source.id,
            tiktok_video_id=f"video-{index}",
            tiktok_url=f"https://www.tiktok.com/@vtv24news/video/video-{index}",
            posted_at=now - timedelta(hours=1),
            is_tracked=True,
            is_deleted=False,
            next_metric_update=None,
        )
        db.add(post)
        posts.append(post)
    db.commit()
    for post in posts:
        db.refresh(post)
    db.refresh(source)
    return source, posts


def _metric_html(stats_key="statsV2"):
    payload = {
        "__DEFAULT_SCOPE__": {
            "webapp.video-detail": {
                "itemInfo": {
                    "itemStruct": {
                        "id": "video-1",
                        "author": {"uniqueId": "vtv24news"},
                        stats_key: {
                            "playCount": "100",
                            "diggCount": "10",
                            "commentCount": "1",
                            "shareCount": "2",
                            "collectCount": "3",
                        },
                    }
                }
            }
        }
    }
    return (
        '<html><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">'
        f"{json.dumps(payload)}"
        "</script></html>"
    )


def test_extract_metrics_from_html_reads_stats_v2():
    metrics = extract_metrics_from_html(_metric_html("statsV2"))

    assert metrics == {
        "video_id": "video-1",
        "author": "vtv24news",
        "views_count": 100,
        "likes_count": 10,
        "comments_count": 1,
        "shares_count": 2,
        "bookmarks_count": 3,
    }


def test_extract_metrics_from_html_falls_back_to_stats():
    metrics = extract_metrics_from_html(_metric_html("stats"))

    assert metrics["views_count"] == 100
    assert metrics["likes_count"] == 10


def test_extract_metrics_from_html_reports_missing_script():
    try:
        extract_metrics_from_html("<html>captcha Please wait</html>")
    except ValueError as exc:
        assert "waf=True" in str(exc)
        assert "captcha=True" in str(exc)
    else:
        raise AssertionError("Expected missing rehydration script to raise")


def test_metric_retry_policy_retries_waf_network_and_transient_http_errors():
    assert metric_service._should_retry_metric_result(
        {"ok": False, "error": "Khong co script. waf=True"}
    )
    assert metric_service._should_retry_metric_result(
        {"ok": False, "error": "Connection timed out"}
    )
    assert metric_service._should_retry_metric_result(
        {"ok": False, "error": "HTTP 403", "status_code": 403}
    )
    assert metric_service._should_retry_metric_result(
        {"ok": False, "error": "TikTok returned Forbidden"}
    )
    assert metric_service._should_retry_metric_result(
        {"ok": False, "error": "HTTP Error 429: Too Many Requests"}
    )
    assert metric_service._should_retry_metric_result(
        {"ok": False, "error": "No video formats found"}
    )
    assert not metric_service._should_retry_metric_result(
        {"ok": False, "error": "HTTP 404", "status_code": 404}
    )
    assert not metric_service._should_retry_metric_result(
        {"ok": False, "error": "Your IP address is blocked from accessing this post", "is_deleted": True}
    )


def test_extract_tiktok_video_metrics_uses_yt_dlp_counts(monkeypatch):
    calls = []

    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts
            calls.append(("init", opts))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            calls.append(("extract_info", url, download))
            return {
                "like_count": "10",
                "repost_count": "2",
                "comment_count": "1",
                "view_count": "100",
                "save_count": "3",
            }

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))

    metrics = metric_service.extract_tiktok_video_metrics(
        "https://www.tiktok.com/@vtv24news/video/video-1",
        timeout=8,
    )

    assert metrics == {
        "likes_count": 10,
        "shares_count": 2,
        "comments_count": 1,
        "views_count": 100,
        "bookmarks_count": 3,
    }
    assert calls[0][1]["socket_timeout"] == 8


def test_extract_tiktok_video_metrics_falls_back_to_share_count(monkeypatch):
    class FakeYoutubeDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            return {"share_count": "5"}

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))

    metrics = metric_service.extract_tiktok_video_metrics("https://www.tiktok.com/@u/video/1")

    assert metrics["shares_count"] == 5


def test_extract_tiktok_video_metrics_falls_back_to_gallery_dl_for_targeted_errors(monkeypatch):
    errors = [
        "[TikTok] 1: Unexpected response from webpage request",
        "[TikTok] 1: Unable to extract universal data for rehydration",
        "[TikTok] 1: No video formats found!",
        "[TikTok] 1: Unable to download webpage: HTTP Error 403: Forbidden",
        "[tiktok:user] foodvietnam: Unable to extract secondary user ID",
    ]

    for error in errors:
        gallery_calls = []

        class FakeYoutubeDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download=False):
                raise Exception(error)

        def fake_extract_tiktok_post(url):
            gallery_calls.append(url)
            return {
                "metrics": {
                    "likes_count": 10,
                    "shares_count": 2,
                    "comments_count": 1,
                    "views_count": 100,
                    "bookmarks_count": 3,
                }
            }

        monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))
        monkeypatch.setattr(gallery_dl_tiktok, "extract_tiktok_post", fake_extract_tiktok_post)

        metrics = metric_service.extract_tiktok_video_metrics("https://www.tiktok.com/@u/video/1")

        assert metrics == {
            "likes_count": 10,
            "shares_count": 2,
            "comments_count": 1,
            "views_count": 100,
            "bookmarks_count": 3,
        }
        assert gallery_calls == ["https://www.tiktok.com/@u/video/1"]


def test_extract_tiktok_video_metrics_raises_combined_error_when_gallery_dl_fails(monkeypatch):
    class FakeYoutubeDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            raise Exception("[TikTok] 1: Unexpected response from webpage request")

    def fake_extract_tiktok_post(url):
        raise gallery_dl_tiktok.GalleryDLTikTokError("gallery failed")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    monkeypatch.setattr(gallery_dl_tiktok, "extract_tiktok_post", fake_extract_tiktok_post)

    try:
        metric_service.extract_tiktok_video_metrics("https://www.tiktok.com/@u/video/1")
    except RuntimeError as exc:
        assert "yt-dlp failed" in str(exc)
        assert "gallery-dl failed: gallery failed" in str(exc)
    else:
        raise AssertionError("Expected combined fallback error")


def test_extract_tiktok_video_metrics_raises_deleted_error(monkeypatch):
    gallery_calls = []

    class FakeYoutubeDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            raise Exception("[TikTok] 123: Your IP address is blocked from accessing this post")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    monkeypatch.setattr(gallery_dl_tiktok, "extract_tiktok_post", lambda url: gallery_calls.append(url))

    try:
        metric_service.extract_tiktok_video_metrics("https://www.tiktok.com/@u/video/123")
    except metric_service.DeletedTikTokVideoError as exc:
        assert "blocked from accessing this post" in str(exc)
    else:
        raise AssertionError("Expected deleted TikTok video error")
    assert gallery_calls == []


def test_update_post_metric_writes_task_log_summary(monkeypatch):
    now = datetime(2026, 1, 2, 12, 0, 0)

    def fake_extract_tiktok_video_metrics(url, timeout=None):
        return {
            "likes_count": 10,
            "shares_count": 2,
            "comments_count": 1,
            "views_count": 100,
            "bookmarks_count": 3,
        }

    monkeypatch.setattr(metric_service, "_now", lambda: now)
    monkeypatch.setattr(metric_service, "extract_tiktok_video_metrics", fake_extract_tiktok_video_metrics)
    db = _session()
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.flush()
    post = Post(
        source_id=source.id,
        tiktok_video_id="video-1",
        tiktok_url="https://www.tiktok.com/@vtv24news/video/video-1",
        posted_at=now - timedelta(hours=1),
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    job = asyncio.run(update_post_metric(db, post))
    task_log = db.query(TaskLog).one()

    assert job.status == "done"
    assert post.metric_tier == "very_low"
    assert post.next_metric_update == post.last_metric_update + timedelta(hours=12)
    assert task_log.task_name == "update_metrics"
    assert task_log.status == "done"
    assert task_log.items_processed == 1
    assert task_log.errors_count == 0
    assert task_log.error_message is None
    assert db.query(PipelineLog).count() == 0


def test_update_post_metric_skips_posts_older_than_24h(monkeypatch):
    now = datetime(2026, 1, 2, 12, 0, 0)

    def fail_if_called(url, timeout=None):
        raise AssertionError("Old posts should not request TikTok video info")

    monkeypatch.setattr(metric_service, "_now", lambda: now)
    monkeypatch.setattr(metric_service, "extract_tiktok_video_metrics", fail_if_called)
    db = _session()
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.flush()
    post = Post(
        source_id=source.id,
        tiktok_video_id="video-old",
        tiktok_url="https://www.tiktok.com/@vtv24news/video/video-old",
        posted_at=now - timedelta(hours=24, seconds=1),
        is_tracked=True,
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    job = asyncio.run(update_post_metric(db, post))
    task_log = db.query(TaskLog).one()

    assert job.status == "done"
    assert job.items_total == 0
    assert job.items_updated == 0
    assert post.is_tracked is False
    assert post.last_metric_update is None
    assert len(post.metrics) == 0
    assert task_log.task_name == "update_metrics"
    assert task_log.items_processed == 0


def test_update_post_metric_records_deleted_video_first_miss(monkeypatch):
    now = datetime(2026, 1, 2, 12, 0, 0)

    def fake_extract_tiktok_video_metrics(url, timeout=None):
        raise metric_service.DeletedTikTokVideoError(
            "[TikTok] video-1: Your IP address is blocked from accessing this post"
        )

    monkeypatch.setattr(metric_service, "_now", lambda: now)
    monkeypatch.setattr(metric_service, "extract_tiktok_video_metrics", fake_extract_tiktok_video_metrics)
    db = _session()
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.flush()
    post = Post(
        source_id=source.id,
        tiktok_video_id="video-1",
        tiktok_url="https://www.tiktok.com/@vtv24news/video/video-1",
        posted_at=now - timedelta(hours=1),
        is_tracked=True,
        is_deleted=False,
        next_metric_update=now,
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    job = asyncio.run(update_post_metric(db, post))

    assert job.status == "failed"
    assert job.items_updated == 0
    assert job.items_failed == 1
    assert post.metric_scan_miss_count == 1
    assert post.is_tracked is True
    assert post.is_deleted is False
    assert post.next_metric_update == now
    assert db.query(PostMetric).count() == 0
    assert db.query(PipelineLog).count() == 1


def test_update_post_metric_marks_deleted_after_second_miss(monkeypatch):
    now = datetime(2026, 1, 2, 12, 0, 0)

    def fake_extract_tiktok_video_metrics(url, timeout=None):
        return None

    monkeypatch.setattr(metric_service, "_now", lambda: now)
    monkeypatch.setattr(metric_service, "extract_tiktok_video_metrics", fake_extract_tiktok_video_metrics)
    db = _session()
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.flush()
    post = Post(
        source_id=source.id,
        tiktok_video_id="video-1",
        tiktok_url="https://www.tiktok.com/@vtv24news/video/video-1",
        posted_at=now - timedelta(hours=1),
        is_tracked=True,
        is_deleted=False,
        next_metric_update=now,
        metric_scan_miss_count=1,
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    job = asyncio.run(update_post_metric(db, post))

    assert job.status == "failed"
    assert job.items_updated == 0
    assert job.items_failed == 1
    assert post.metric_scan_miss_count == 2
    assert post.is_tracked is False
    assert post.is_deleted is True
    assert post.next_metric_update is None
    assert db.query(PostMetric).count() == 0
    assert db.query(PipelineLog).count() == 1


def test_update_post_metric_logs_only_after_gallery_dl_fallback_fails(monkeypatch):
    now = datetime(2026, 1, 2, 12, 0, 0)

    class FakeYoutubeDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            raise Exception("[TikTok] video-1: Unexpected response from webpage request")

    def fake_extract_tiktok_post(url):
        raise gallery_dl_tiktok.GalleryDLTikTokError("gallery unavailable")

    monkeypatch.setattr(metric_service, "_now", lambda: now)
    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    monkeypatch.setattr(gallery_dl_tiktok, "extract_tiktok_post", fake_extract_tiktok_post)
    db = _session()
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.flush()
    post = Post(
        source_id=source.id,
        tiktok_video_id="video-1",
        tiktok_url="https://www.tiktok.com/@vtv24news/video/video-1",
        posted_at=now - timedelta(hours=1),
        is_tracked=True,
        is_deleted=False,
        next_metric_update=now,
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    job = asyncio.run(update_post_metric(db, post))
    pipeline_log = db.query(PipelineLog).one()

    assert job.status == "failed"
    assert job.items_failed == 1
    assert "yt-dlp failed" in pipeline_log.error_details
    assert "gallery-dl failed: gallery unavailable" in pipeline_log.error_details


def test_update_source_metrics_bulk_updates_due_posts(monkeypatch):
    now = datetime(2026, 1, 2, 12, 0, 0)
    monkeypatch.setattr(metric_service, "_now", lambda: now)
    db = _session()
    _active_tiktok_session(db)
    source, posts = _source_with_posts(db, now, count=3)
    posts[1].metric_scan_miss_count = 1
    db.commit()

    async def fake_fetch_metric_results(posts_arg, session_record):
        return [
            {
                "post_id": post.id,
                "url": post.tiktok_url,
                "ok": True,
                "metrics": {
                    "views_count": 100,
                    "likes_count": 10,
                    "comments_count": 1,
                    "shares_count": 2,
                    "bookmarks_count": 3,
                },
            }
            for post in posts_arg
        ]

    monkeypatch.setattr(metric_service, "_fetch_metric_results", fake_fetch_metric_results)

    job = asyncio.run(update_source_metrics(db, source))

    assert job.status == "done"
    assert job.items_total == 3
    assert job.items_updated == 3
    assert job.items_failed == 0
    assert db.query(PostMetric).count() == 3
    for post in posts:
        db.refresh(post)
        assert post.metric_scan_miss_count == 0
        assert post.last_metric_update == now
        assert post.metric_tier == "very_low"
        assert post.next_metric_update == now + timedelta(hours=12)
    task_log = db.query(TaskLog).one()
    assert task_log.items_processed == 3


def test_update_source_metrics_records_failed_posts(monkeypatch):
    now = datetime(2026, 1, 2, 12, 0, 0)
    monkeypatch.setattr(metric_service, "_now", lambda: now)
    db = _session()
    _active_tiktok_session(db)
    source, posts = _source_with_posts(db, now, count=2)

    async def fake_fetch_metric_results(posts_arg, session_record):
        return [
            {
                "post_id": posts_arg[0].id,
                "url": posts_arg[0].tiktok_url,
                "ok": True,
                "metrics": {
                    "views_count": 100,
                    "likes_count": 10,
                    "comments_count": 1,
                    "shares_count": 2,
                    "bookmarks_count": 3,
                },
            },
            {
                "post_id": posts_arg[1].id,
                "url": posts_arg[1].tiktok_url,
                "ok": False,
                "error": "HTTP 403",
            },
        ]

    monkeypatch.setattr(metric_service, "_fetch_metric_results", fake_fetch_metric_results)

    job = asyncio.run(update_source_metrics(db, source))

    assert job.status == "done"
    assert job.items_updated == 1
    assert job.items_failed == 1
    assert db.query(PostMetric).count() == 1
    assert db.query(PipelineLog).count() == 1
    assert "HTTP 403" in job.error_message
    db.refresh(posts[1])
    assert posts[1].metric_scan_miss_count == 1
    assert posts[1].is_tracked is True
    assert posts[1].is_deleted is False
    assert posts[1].last_metric_update is None


def test_update_source_metrics_records_deleted_post_first_miss(monkeypatch):
    now = datetime(2026, 1, 2, 12, 0, 0)
    monkeypatch.setattr(metric_service, "_now", lambda: now)
    db = _session()
    _active_tiktok_session(db)
    source, posts = _source_with_posts(db, now, count=2)
    posts[1].next_metric_update = now
    db.commit()

    async def fake_fetch_metric_results(posts_arg, session_record):
        return [
            {
                "post_id": posts_arg[0].id,
                "url": posts_arg[0].tiktok_url,
                "ok": True,
                "metrics": {
                    "views_count": 100,
                    "likes_count": 10,
                    "comments_count": 1,
                    "shares_count": 2,
                    "bookmarks_count": 3,
                },
            },
            {
                "post_id": posts_arg[1].id,
                "url": posts_arg[1].tiktok_url,
                "ok": False,
                "is_deleted": True,
                "error": "Your IP address is blocked from accessing this post",
            },
        ]

    monkeypatch.setattr(metric_service, "_fetch_metric_results", fake_fetch_metric_results)

    job = asyncio.run(update_source_metrics(db, source))

    assert job.status == "done"
    assert job.items_updated == 1
    assert job.items_failed == 1
    assert db.query(PostMetric).count() == 1
    assert db.query(PipelineLog).count() == 1
    db.refresh(posts[1])
    assert posts[1].metric_scan_miss_count == 1
    assert posts[1].is_tracked is True
    assert posts[1].is_deleted is False
    assert posts[1].next_metric_update == now


def test_update_source_metrics_marks_post_deleted_after_second_miss(monkeypatch):
    now = datetime(2026, 1, 2, 12, 0, 0)
    monkeypatch.setattr(metric_service, "_now", lambda: now)
    db = _session()
    _active_tiktok_session(db)
    source, posts = _source_with_posts(db, now, count=1)
    posts[0].metric_scan_miss_count = 1
    posts[0].next_metric_update = now
    db.commit()

    async def fake_fetch_metric_results(posts_arg, session_record):
        return [
            {
                "post_id": posts_arg[0].id,
                "url": posts_arg[0].tiktok_url,
                "ok": False,
                "error": "Connection reset by peer",
            },
        ]

    monkeypatch.setattr(metric_service, "_fetch_metric_results", fake_fetch_metric_results)

    job = asyncio.run(update_source_metrics(db, source))

    assert job.status == "failed"
    assert job.items_updated == 0
    assert job.items_failed == 1
    assert db.query(PostMetric).count() == 0
    assert db.query(PipelineLog).count() == 1
    db.refresh(posts[0])
    assert posts[0].metric_scan_miss_count == 2
    assert posts[0].is_tracked is False
    assert posts[0].is_deleted is True
    assert posts[0].next_metric_update is None


def test_update_source_metrics_with_no_due_posts_does_not_fetch(monkeypatch):
    now = datetime(2026, 1, 2, 12, 0, 0)
    monkeypatch.setattr(metric_service, "_now", lambda: now)
    db = _session()
    _active_tiktok_session(db)
    source, posts = _source_with_posts(db, now, count=1)
    posts[0].next_metric_update = now + timedelta(hours=1)
    db.commit()

    async def fail_if_called(posts_arg, session_record):
        raise AssertionError("No due posts should be fetched")

    monkeypatch.setattr(metric_service, "_fetch_metric_results", fail_if_called)

    job = asyncio.run(update_source_metrics(db, source))

    assert job.status == "done"
    assert job.items_total == 0
    assert job.items_updated == 0
    assert db.query(PostMetric).count() == 0


def test_update_source_metrics_does_not_require_active_session(monkeypatch):
    now = datetime(2026, 1, 2, 12, 0, 0)
    monkeypatch.setattr(metric_service, "_now", lambda: now)
    db = _session()
    source, posts = _source_with_posts(db, now, count=1)

    async def fake_fetch_metric_results(posts_arg, session_record):
        assert session_record is None
        return [
            {
                "post_id": posts[0].id,
                "url": posts[0].tiktok_url,
                "ok": True,
                "metrics": {
                    "views_count": 100,
                    "likes_count": 10,
                    "comments_count": 1,
                    "shares_count": 2,
                    "bookmarks_count": 3,
                },
            }
        ]

    monkeypatch.setattr(metric_service, "_fetch_metric_results", fake_fetch_metric_results)

    job = asyncio.run(update_source_metrics(db, source))

    assert job.status == "done"
    assert job.items_total == 1
    assert job.items_updated == 1
    assert job.items_failed == 0
    assert db.query(PostMetric).count() == 1


def test_update_source_metrics_marks_old_passed_posts_untracked(monkeypatch):
    now = datetime(2026, 1, 2, 12, 0, 0)
    monkeypatch.setattr(metric_service, "_now", lambda: now)
    db = _session()
    _active_tiktok_session(db)
    source, posts = _source_with_posts(db, now, count=1)
    posts[0].posted_at = now - timedelta(hours=24, seconds=1)
    db.commit()
    db.refresh(posts[0])

    async def fail_if_called(posts_arg, session_record):
        raise AssertionError("Old posts should not be fetched")

    monkeypatch.setattr(metric_service, "_fetch_metric_results", fail_if_called)

    job = asyncio.run(update_source_metrics(db, source, posts=posts, now=now))

    assert job.status == "done"
    assert job.items_total == 0
    db.refresh(posts[0])
    assert posts[0].is_tracked is False


def test_metric_worker_delays_between_posts_in_same_worker(monkeypatch):
    monkeypatch.setattr(
        metric_service,
        "get_settings",
        lambda: SimpleNamespace(
            metric_impersonate="chrome124",
            metric_timeout_seconds=15,
            metric_max_retries=0,
            metric_retry_delay_seconds=2,
            metric_request_delay_seconds=0.25,
        ),
    )

    fetch_calls = []
    sleep_calls = []

    async def fake_fetch_one_metric(post, worker_id, timeout):
        fetch_calls.append(post.id)
        return {
            "post_id": post.id,
            "url": post.tiktok_url,
            "worker": worker_id,
            "ok": True,
            "metrics": {},
        }

    async def fake_sleep(delay):
        sleep_calls.append(delay)

    monkeypatch.setattr(metric_service, "_fetch_one_metric", fake_fetch_one_metric)
    monkeypatch.setattr(metric_service.asyncio, "sleep", fake_sleep)

    queue = asyncio.Queue()
    queue.put_nowait(SimpleNamespace(id=1, tiktok_url="https://example.test/1"))
    queue.put_nowait(SimpleNamespace(id=2, tiktok_url="https://example.test/2"))
    results = []
    session_record = SimpleNamespace(sessionid="sessionid", tt_csrf_token="csrf")

    asyncio.run(metric_service._metric_worker(1, queue, results, session_record))

    assert fetch_calls == [1, 2]
    assert sleep_calls == [0.25]
    assert [result["post_id"] for result in results] == [1, 2]
