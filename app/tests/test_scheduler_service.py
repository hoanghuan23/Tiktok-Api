import asyncio
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

    async def fake_crawl_source(db_arg, source_arg, max_count=30):
        calls["sources"].append((source_arg.id, max_count))
        source_arg.next_scrape = now + timedelta(minutes=30)
        return SimpleNamespace(id=101)

    async def fake_update_post_metric(db_arg, post_arg):
        calls["posts"].append(post_arg.id)
        post_arg.next_metric_update = now + timedelta(seconds=200)
        return SimpleNamespace(id=202)

    monkeypatch.setattr(scheduler_service, "crawl_source", fake_crawl_source)
    monkeypatch.setattr(scheduler_service, "update_post_metric", fake_update_post_metric)

    result = asyncio.run(
        scheduler_service.run_scheduler_cycle(db, now=now, source_limit=20, post_limit=50)
    )

    assert result == {
        "sources_processed": 1,
        "posts_processed": 1,
        "source_job_ids": [101],
        "post_job_ids": [202],
    }
    assert calls == {"sources": [(source.id, 30)], "posts": [post.id]}
    db.close()
