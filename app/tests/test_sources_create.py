import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sys

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.models import PipelineJob, Post, PostMetric, Source
from app.routers.sources import create_source
from app.schemas.sources import SourceCreate
from app.services.tiktok_client import TikTokClient


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return session_local()


def _timestamp(value):
    return int(value.replace(tzinfo=timezone.utc).timestamp())


def _install_fake_youtube_dl(monkeypatch, entries, calls):
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
            return {"entries": entries}

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))


def test_create_user_source_uses_tiktok_url_uploader_and_bootstrap_posts(monkeypatch):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    entries = [
        {
            "id": "video-1",
            "timestamp": _timestamp(now - timedelta(hours=1)),
            "webpage_url": "https://www.tiktok.com/@vtv24news/video/video-1",
            "description": "Ban tin moi #news",
            "uploader": "vtv24news",
            "duration": 42,
            "thumbnail": "https://example.com/cover.jpg",
            "view_count": "1200",
            "like_count": "100",
            "comment_count": "7",
            "share_count": "5",
            "save_count": "3",
        },
    ]
    calls = []
    _install_fake_youtube_dl(monkeypatch, entries, calls)
    db = _session()

    source = asyncio.run(
        create_source(
            SourceCreate(
                source_type="user",
                tiktok_url="https://www.tiktok.com/@vtv24news",
                display_name="VTV24",
                max_days_old=1,
            ),
            db,
        )
    )

    saved_source = db.get(Source, source.id)
    post = db.query(Post).filter(Post.tiktok_video_id == "video-1").one()
    metric = db.query(PostMetric).filter(PostMetric.post_id == post.id).one()
    job = db.query(PipelineJob).filter(PipelineJob.source_id == saved_source.id).one()

    assert calls[1] == ("extract_info", "https://www.tiktok.com/@vtv24news", False)
    assert saved_source.identifier == "vtv24news"
    assert saved_source.tiktok_url == "https://www.tiktok.com/@vtv24news"
    assert post.source_id == saved_source.id
    assert post.tiktok_url == "https://www.tiktok.com/@vtv24news/video/video-1"
    assert post.description == "Ban tin moi #news"
    assert metric.views_count == 1200
    assert metric.likes_count == 100
    assert job.status == "done"
    assert job.posts_new == 1


def test_create_user_source_rejects_missing_yt_dlp_uploader(monkeypatch):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    entries = [{"id": "video-1", "timestamp": _timestamp(now - timedelta(hours=1))}]
    _install_fake_youtube_dl(monkeypatch, entries, [])
    db = _session()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            create_source(
                SourceCreate(source_type="user", tiktok_url="https://www.tiktok.com/@vtv24news"),
                db,
            )
        )

    assert exc_info.value.status_code == 400
    assert "identifier" in exc_info.value.detail
    assert db.query(Source).count() == 0


def test_create_hashtag_source_keeps_identifier_contract(monkeypatch):
    async def fail_if_called(self, profile_url, max_count, since=None):
        raise AssertionError("user profile crawl should only run for user sources")

    monkeypatch.setattr(TikTokClient, "get_user_profile_videos", fail_if_called)
    db = _session()

    source = asyncio.run(
        create_source(
            SourceCreate(source_type="hashtag", identifier="#python"),
            db,
        )
    )

    saved_source = db.get(Source, source.id)
    assert saved_source.identifier == "python"
    assert saved_source.tiktok_url == "https://www.tiktok.com/tag/python"
    assert db.query(PipelineJob).count() == 0
