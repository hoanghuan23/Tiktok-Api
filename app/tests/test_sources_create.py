import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.models import Source
from app.routers.sources import create_source
from app.schemas.sources import SourceCreate
from app.services.tiktok_client import TikTokClient


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return session_local()


def test_create_user_source_fetches_and_stores_follower_count(monkeypatch):
    calls = []

    async def fake_get_user_follower_count(self, username):
        calls.append(username)
        return 12345

    monkeypatch.setattr(TikTokClient, "get_user_follower_count", fake_get_user_follower_count)
    db = _session()

    source = asyncio.run(
        create_source(
            SourceCreate(source_type="user", identifier="@khoailangthang", display_name="Khoai"),
            db,
        )
    )

    saved_source = db.get(Source, source.id)
    assert calls == ["khoailangthang"]
    assert saved_source.identifier == "khoailangthang"
    assert saved_source.tiktok_url == "https://www.tiktok.com/@khoailangthang"
    assert saved_source.follower_count == 12345


def test_create_hashtag_source_does_not_fetch_follower_count(monkeypatch):
    async def fail_if_called(self, username):
        raise AssertionError("TikTok user info should not be fetched for hashtag sources")

    monkeypatch.setattr(TikTokClient, "get_user_follower_count", fail_if_called)
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
    assert saved_source.follower_count is None
