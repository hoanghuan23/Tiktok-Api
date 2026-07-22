import asyncio
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.models import Post, Source
from app.services import scheduler_service


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return session_local()


def _file_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'scheduler.db'}",
        connect_args={"check_same_thread": False},
    )
    models.Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _settings(workers=3):
    return SimpleNamespace(
        scheduler_source_batch_size=15,
        scheduler_post_batch_size=30,
        scheduler_num_workers=workers,
    )


def test_due_sources_includes_null_next_scrape_and_limits_batch():
    db = _session()
    now = datetime(2026, 1, 2, 12, 0, 0)
    for index in range(25):
        db.add(
            Source(
                source_type="hashtag",
                identifier=f"tag-{index}",
                is_active=True,
                is_accessible=True,
                next_scrape=None,
            )
        )
    db.add(
        Source(
            source_type="hashtag",
            identifier="future",
            is_active=True,
            is_accessible=True,
            next_scrape=now + timedelta(hours=1),
        )
    )
    db.commit()

    sources = scheduler_service.due_sources(db, now, limit=20)

    assert len(sources) == 20
    assert all(source.next_scrape is None for source in sources)
    db.close()


def test_due_posts_includes_null_next_metric_update_and_limits_batch():
    db = _session()
    now = datetime(2026, 1, 2, 12, 0, 0)
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.flush()
    for index in range(55):
        db.add(
            Post(
                source_id=source.id,
                tiktok_video_id=f"video-{index}",
                tiktok_url=f"https://www.tiktok.com/@vtv24news/video/video-{index}",
                posted_at=now,
                is_tracked=True,
                is_deleted=False,
                next_metric_update=None,
            )
        )
    db.add(
        Post(
            source_id=source.id,
            tiktok_video_id="future",
            tiktok_url="https://www.tiktok.com/@vtv24news/video/future",
            posted_at=now,
            is_tracked=True,
            is_deleted=False,
            next_metric_update=now + timedelta(hours=1),
        )
    )
    db.commit()

    posts = scheduler_service.due_posts(db, now, limit=50)

    assert len(posts) == 50
    assert all(post.next_metric_update is None for post in posts)
    db.close()


def test_due_posts_excludes_posts_older_than_24h():
    db = _session()
    now = datetime(2026, 1, 2, 12, 0, 0)
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.flush()
    recent_post = Post(
        source_id=source.id,
        tiktok_video_id="recent",
        tiktok_url="https://www.tiktok.com/@vtv24news/video/recent",
        posted_at=now - timedelta(hours=23, minutes=59),
        is_tracked=True,
        is_deleted=False,
        next_metric_update=None,
    )
    old_post = Post(
        source_id=source.id,
        tiktok_video_id="old",
        tiktok_url="https://www.tiktok.com/@vtv24news/video/old",
        posted_at=now - timedelta(hours=24, seconds=1),
        is_tracked=True,
        is_deleted=False,
        next_metric_update=None,
    )
    db.add_all([recent_post, old_post])
    db.commit()

    posts = scheduler_service.due_posts(db, now)

    assert [post.tiktok_video_id for post in posts] == ["recent"]
    db.close()


def test_expire_old_tracked_posts_marks_posts_untracked():
    db = _session()
    now = datetime(2026, 1, 2, 12, 0, 0)
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.flush()
    recent_post = Post(
        source_id=source.id,
        tiktok_video_id="recent-expire",
        tiktok_url="https://www.tiktok.com/@vtv24news/video/recent-expire",
        posted_at=now - timedelta(hours=23, minutes=59),
        is_tracked=True,
        is_deleted=False,
    )
    old_post = Post(
        source_id=source.id,
        tiktok_video_id="old-expire",
        tiktok_url="https://www.tiktok.com/@vtv24news/video/old-expire",
        posted_at=now - timedelta(hours=24, seconds=1),
        is_tracked=True,
        is_deleted=False,
    )
    db.add_all([recent_post, old_post])
    db.commit()

    expired_count = scheduler_service.expire_old_tracked_posts(db, now)
    db.refresh(recent_post)
    db.refresh(old_post)

    assert expired_count == 1
    assert recent_post.is_tracked is True
    assert old_post.is_tracked is False
    db.close()


def test_run_scheduler_cycle_processes_due_source_and_post_batches(monkeypatch):
    db = _session()
    now = datetime(2026, 1, 2, 12, 0, 0)
    source = Source(
        source_type="keyword",
        identifier="news",
        is_active=True,
        is_accessible=True,
        next_scrape=None,
    )
    db.add(source)
    db.flush()
    post = Post(
        source_id=source.id,
        tiktok_video_id="video-1",
        tiktok_url="https://www.tiktok.com/@author/video/video-1",
        posted_at=now,
        is_tracked=True,
        is_deleted=False,
        next_metric_update=None,
    )
    db.add(post)
    db.commit()
    calls = {"sources": [], "posts": []}

    def fake_source_job(source_id, max_count=10):
        calls["sources"].append((source_id, max_count))
        return 101

    def fake_metric_job(source_id, post_ids, current_time):
        calls["posts"].append((source_id, post_ids, current_time))
        return 202

    monkeypatch.setattr(scheduler_service, "get_settings", lambda: _settings())
    monkeypatch.setattr(scheduler_service, "_run_source_job_in_thread", fake_source_job)
    monkeypatch.setattr(scheduler_service, "_run_metric_job_in_thread", fake_metric_job)

    result = asyncio.run(
        scheduler_service.run_scheduler_cycle(db, now=now, source_limit=20, post_limit=50)
    )

    assert result == {
        "sources_processed": 1,
        "posts_processed": 1,
        "posts_expired": 0,
        "source_job_ids": [101],
        "post_job_ids": [202],
    }
    assert calls == {"sources": [(source.id, 30)], "posts": [(source.id, [post.id], now)]}
    db.close()


def test_run_scheduler_cycle_groups_due_posts_by_source(monkeypatch):
    db = _session()
    now = datetime(2026, 1, 2, 12, 0, 0)
    source_a = Source(source_type="user", identifier="a", is_active=True, next_scrape=now + timedelta(hours=1))
    source_b = Source(source_type="user", identifier="b", is_active=True, next_scrape=now + timedelta(hours=1))
    db.add_all([source_a, source_b])
    db.flush()
    posts = [
        Post(
            source_id=source_a.id,
            tiktok_video_id="a-1",
            tiktok_url="https://www.tiktok.com/@a/video/a-1",
            posted_at=now,
            is_tracked=True,
            is_deleted=False,
            next_metric_update=None,
        ),
        Post(
            source_id=source_a.id,
            tiktok_video_id="a-2",
            tiktok_url="https://www.tiktok.com/@a/video/a-2",
            posted_at=now,
            is_tracked=True,
            is_deleted=False,
            next_metric_update=None,
        ),
        Post(
            source_id=source_b.id,
            tiktok_video_id="b-1",
            tiktok_url="https://www.tiktok.com/@b/video/b-1",
            posted_at=now,
            is_tracked=True,
            is_deleted=False,
            next_metric_update=None,
        ),
    ]
    db.add_all(posts)
    db.commit()
    calls = []

    def fake_metric_job(source_id, post_ids, current_time):
        calls.append((source_id, post_ids, current_time))
        return 200 + source_id

    monkeypatch.setattr(scheduler_service, "get_settings", lambda: _settings())
    monkeypatch.setattr(scheduler_service, "_run_metric_job_in_thread", fake_metric_job)

    result = asyncio.run(
        scheduler_service.run_scheduler_cycle(db, now=now, source_limit=0, post_limit=50)
    )

    assert result["posts_processed"] == 3
    assert result["post_job_ids"] == [201, 202]
    assert sorted(calls) == [
        (source_a.id, [posts[0].id, posts[1].id], now),
        (source_b.id, [posts[2].id], now),
    ]
    db.close()


def test_run_source_job_in_thread_uses_own_session(monkeypatch, tmp_path):
    session_local = _file_session_factory(tmp_path)
    db = session_local()
    source = Source(source_type="keyword", identifier="news", is_active=True, is_accessible=True)
    db.add(source)
    db.commit()
    source_id = source.id
    db.close()
    calls = []

    async def fake_crawl_source(db_arg, source_arg, max_count=10):
        calls.append((source_arg.id, source_arg.identifier, max_count))
        return SimpleNamespace(id=301)

    monkeypatch.setattr(scheduler_service, "SessionLocal", session_local)
    monkeypatch.setattr(scheduler_service, "crawl_source", fake_crawl_source)

    assert scheduler_service._run_source_job_in_thread(source_id, 25) == 301
    assert calls == [(source_id, "news", 25)]


def test_run_metric_job_in_thread_uses_own_session_and_preserves_post_order(monkeypatch, tmp_path):
    session_local = _file_session_factory(tmp_path)
    db = session_local()
    source = Source(source_type="user", identifier="author", is_active=True, is_accessible=True)
    db.add(source)
    db.flush()
    first = Post(
        source_id=source.id,
        tiktok_video_id="first",
        tiktok_url="https://www.tiktok.com/@author/video/first",
        posted_at=datetime(2026, 1, 2, 12, 0, 0),
        is_tracked=True,
        is_deleted=False,
    )
    second = Post(
        source_id=source.id,
        tiktok_video_id="second",
        tiktok_url="https://www.tiktok.com/@author/video/second",
        posted_at=datetime(2026, 1, 2, 12, 0, 0),
        is_tracked=True,
        is_deleted=False,
    )
    db.add_all([first, second])
    db.commit()
    source_id = source.id
    post_ids = [second.id, first.id]
    db.close()
    now = datetime(2026, 1, 2, 12, 30, 0)
    calls = []

    async def fake_update_source_metrics(db_arg, source_arg, posts=None, now=None):
        calls.append((source_arg.id, [post.tiktok_video_id for post in posts], now))
        return SimpleNamespace(id=302)

    monkeypatch.setattr(scheduler_service, "SessionLocal", session_local)
    monkeypatch.setattr(scheduler_service, "update_source_metrics", fake_update_source_metrics)

    assert scheduler_service._run_metric_job_in_thread(source_id, post_ids, now) == 302
    assert calls == [(source_id, ["second", "first"], now)]


def test_run_scheduler_cycle_continues_when_worker_fails(monkeypatch):
    db = _session()
    now = datetime(2026, 1, 2, 12, 0, 0)
    source_a = Source(source_type="keyword", identifier="a", is_active=True, is_accessible=True)
    source_b = Source(source_type="keyword", identifier="b", is_active=True, is_accessible=True)
    db.add_all([source_a, source_b])
    db.commit()

    def fake_source_job(source_id, max_count=10):
        if source_id == source_a.id:
            raise RuntimeError("boom")
        return 400 + source_id

    monkeypatch.setattr(scheduler_service, "get_settings", lambda: _settings())
    monkeypatch.setattr(scheduler_service, "_run_source_job_in_thread", fake_source_job)

    result = asyncio.run(
        scheduler_service.run_scheduler_cycle(db, now=now, source_limit=20, post_limit=0)
    )

    assert result["sources_processed"] == 1
    assert result["source_job_ids"] == [400 + source_b.id]
    db.close()


def test_run_scheduler_cycle_limits_scheduler_workers(monkeypatch):
    db = _session()
    now = datetime(2026, 1, 2, 12, 0, 0)
    for index in range(5):
        db.add(
            Source(
                source_type="keyword",
                identifier=f"source-{index}",
                is_active=True,
                is_accessible=True,
            )
        )
    db.commit()
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_source_job(source_id, max_count=10):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return source_id

    monkeypatch.setattr(scheduler_service, "get_settings", lambda: _settings(workers=2))
    monkeypatch.setattr(scheduler_service, "_run_source_job_in_thread", fake_source_job)

    result = asyncio.run(
        scheduler_service.run_scheduler_cycle(db, now=now, source_limit=5, post_limit=0)
    )

    assert result["sources_processed"] == 5
    assert max_active == 2
    db.close()
