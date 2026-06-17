from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sys

import pytest

from app.services.tiktok_client import TikTokClient


class _AsyncVideos:
    def __init__(self, videos):
        self.videos = videos

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for video in self.videos:
            yield video


class _FakeUser:
    def __init__(self, videos):
        self._videos = videos

    def videos(self, count):
        return _AsyncVideos(self._videos[:count])


class _FakeApi:
    def __init__(self, videos):
        self._videos = videos
        self.closed = False

    def user(self, username):
        return _FakeUser(self._videos)

    async def close_sessions(self):
        self.closed = True


@pytest.mark.asyncio
async def test_get_user_videos_stops_when_video_is_older_than_since():
    now = datetime(2026, 1, 2, 12, 0, 0)
    videos = [
        SimpleNamespace(id="1", create_time=now - timedelta(hours=1)),
        SimpleNamespace(id="2", create_time=now - timedelta(hours=23, minutes=59)),
        SimpleNamespace(id="3", create_time=now - timedelta(hours=25)),
        SimpleNamespace(id="4", create_time=now - timedelta(hours=26)),
    ]
    api = _FakeApi(videos)
    client = TikTokClient(db=None)

    async def fake_create_api():
        return api

    client._create_api = fake_create_api

    recent_videos = await client.get_user_videos("vtv24news", max_count=10, since=now - timedelta(hours=24))

    assert [video.id for video in recent_videos] == ["1", "2"]
    assert api.closed is True


@pytest.mark.asyncio
async def test_get_user_videos_uses_create_time_from_as_dict_when_attribute_is_missing():
    now = datetime(2026, 1, 2, 12, 0, 0)

    def timestamp(value):
        return int(value.replace(tzinfo=timezone.utc).timestamp())

    videos = [
        SimpleNamespace(id="1", as_dict={"createTime": timestamp(now - timedelta(hours=1))}),
        SimpleNamespace(id="2", as_dict={"createTime": timestamp(now - timedelta(hours=25))}),
        SimpleNamespace(id="3", as_dict={"createTime": timestamp(now - timedelta(hours=26))}),
    ]
    api = _FakeApi(videos)
    client = TikTokClient(db=None)

    async def fake_create_api():
        return api

    client._create_api = fake_create_api

    recent_videos = await client.get_user_videos("vtv24news", max_count=10, since=now - timedelta(hours=24))

    assert [video.id for video in recent_videos] == ["1"]


@pytest.mark.asyncio
async def test_create_api_passes_configured_session_options(monkeypatch):
    created_kwargs = {}

    class FakeTikTokApi:
        async def create_sessions(self, **kwargs):
            created_kwargs.update(kwargs)

    monkeypatch.setitem(sys.modules, "TikTokApi", SimpleNamespace(TikTokApi=FakeTikTokApi))

    client = TikTokClient(db=None)
    client.settings = SimpleNamespace(
        ms_token="token-123",
        tiktok_headless=False,
        tiktok_browser="chromium",
        tiktok_sleep_after=5,
    )
    client.get_session_record = lambda: None

    await client._create_api()

    assert created_kwargs == {
        "num_sessions": 1,
        "headless": False,
        "browser": "chromium",
        "sleep_after": 5,
        "ms_tokens": ["token-123"],
    }
