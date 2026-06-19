import asyncio
import json
import sys
from datetime import datetime, timedelta
from types import ModuleType, SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.models import PipelineLog, Post, PostMetric, Source, TaskLog, TikTokSession
from app.services import metric_service
from app.services.metric_service import extract_metrics_from_html, update_post_metric, update_source_metrics
from app.services.tiktok_client import TikTokClient


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


def test_update_post_metric_writes_task_log_summary(monkeypatch):
    now = datetime(2026, 1, 2, 12, 0, 0)

    async def fake_get_video_info(self, url):
        return {
            "statsV2": {
                "diggCount": "10",
                "shareCount": "2",
                "commentCount": "1",
                "playCount": "100",
                "collectCount": "3",
            }
        }

    monkeypatch.setattr(metric_service, "_now", lambda: now)
    monkeypatch.setattr(TikTokClient, "get_video_info", fake_get_video_info)
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
    assert post.next_metric_update == metric_service.next_metric_update_at(post.last_metric_update)
    assert task_log.task_name == "update_metrics"
    assert task_log.status == "done"
    assert task_log.items_processed == 1
    assert task_log.errors_count == 0
    assert task_log.error_message is None
    assert db.query(PipelineLog).count() == 0


def test_update_post_metric_skips_posts_older_than_24h(monkeypatch):
    now = datetime(2026, 1, 2, 12, 0, 0)

    async def fail_if_called(self, url):
        raise AssertionError("Old posts should not request TikTok video info")

    monkeypatch.setattr(metric_service, "_now", lambda: now)
    monkeypatch.setattr(TikTokClient, "get_video_info", fail_if_called)
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


def test_update_source_metrics_bulk_updates_due_posts(monkeypatch):
    now = datetime(2026, 1, 2, 12, 0, 0)
    monkeypatch.setattr(metric_service, "_now", lambda: now)
    db = _session()
    _active_tiktok_session(db)
    source, posts = _source_with_posts(db, now, count=3)

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
        assert post.last_metric_update == now
        assert post.next_metric_update == metric_service.next_metric_update_at(now)
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
    assert posts[1].last_metric_update is None


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


def test_update_source_metrics_fails_without_active_session(monkeypatch):
    now = datetime(2026, 1, 2, 12, 0, 0)
    monkeypatch.setattr(metric_service, "_now", lambda: now)
    db = _session()
    source, _posts = _source_with_posts(db, now, count=1)

    async def fail_if_called(posts_arg, session_record):
        raise AssertionError("Missing session should stop before fetch")

    monkeypatch.setattr(metric_service, "_fetch_metric_results", fail_if_called)

    job = asyncio.run(update_source_metrics(db, source))

    assert job.status == "failed"
    assert job.items_total == 1
    assert job.items_failed == 1
    assert "session" in job.error_message.lower()
    assert db.query(PostMetric).count() == 0


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
    class FakeAsyncSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    curl_package = ModuleType("curl_cffi")
    requests_module = ModuleType("curl_cffi.requests")
    requests_module.AsyncSession = FakeAsyncSession
    curl_package.requests = requests_module
    monkeypatch.setitem(sys.modules, "curl_cffi", curl_package)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", requests_module)
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

    async def fake_fetch_one_metric(session, post, worker_id, timeout):
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
