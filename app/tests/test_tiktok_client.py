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


class _FakeSearch:
    def __init__(self, videos, calls):
        self._videos = videos
        self._calls = calls

    def search_type(self, keyword, search_type):
        self._calls.append((keyword, search_type))
        return _AsyncVideos(self._videos)


class _FakeSearchApi:
    def __init__(self, videos):
        self.calls = []
        self.search = _FakeSearch(videos, self.calls)
        self.closed = False

    async def close_sessions(self):
        self.closed = True


class _FakeHashtag:
    def __init__(self, videos, calls):
        self._videos = videos
        self._calls = calls

    def videos(self, count):
        self._calls.append(count)
        return _AsyncVideos(self._videos[:count])


class _FakeHashtagApi:
    def __init__(self, videos):
        self.calls = []
        self.hashtag_names = []
        self._videos = videos
        self.closed = False

    def hashtag(self, name):
        self.hashtag_names.append(name)
        return _FakeHashtag(self._videos, self.calls)

    async def close_sessions(self):
        self.closed = True


def _timestamp(value):
    return int(value.replace(tzinfo=timezone.utc).timestamp())


def _source_video(video_id, created_at, stats=None, stats_key="stats"):
    data = {"id": video_id}
    if created_at is not None:
        data["createTime"] = _timestamp(created_at)
    if stats is not None:
        data[stats_key] = stats
    return SimpleNamespace(id=video_id, as_dict=data)


def _keyword_video(video_id, created_at, stats=None, stats_key="stats"):
    return _source_video(video_id, created_at, stats, stats_key)


def _hashtag_video(video_id, created_at, stats=None, stats_key="stats"):
    return _source_video(video_id, created_at, stats, stats_key)


@pytest.mark.asyncio
async def test_get_user_videos_stops_after_five_consecutive_old_videos():
    now = datetime(2026, 1, 2, 12, 0, 0)
    videos = [
        SimpleNamespace(id="1", create_time=now - timedelta(hours=1)),
        SimpleNamespace(id="2", create_time=now - timedelta(hours=23, minutes=59)),
        SimpleNamespace(id="3", create_time=now - timedelta(hours=25)),
        SimpleNamespace(id="4", create_time=now - timedelta(hours=26)),
        SimpleNamespace(id="5", create_time=now - timedelta(hours=27)),
        SimpleNamespace(id="6", create_time=now - timedelta(hours=28)),
        SimpleNamespace(id="7", create_time=now - timedelta(hours=29)),
        SimpleNamespace(id="8", create_time=now - timedelta(minutes=30)),
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
async def test_get_user_videos_skips_old_pinned_videos_before_recent_videos():
    now = datetime(2026, 1, 2, 12, 0, 0)
    videos = [
        SimpleNamespace(
            id="pinned-old-1",
            create_time=now - timedelta(days=30),
            as_dict={"isPinnedPost": True},
        ),
        SimpleNamespace(
            id="pinned-old-2",
            create_time=now - timedelta(days=10),
            as_dict={"isPinned": True},
        ),
        SimpleNamespace(id="recent-1", create_time=now - timedelta(hours=1)),
        SimpleNamespace(id="recent-2", create_time=now - timedelta(hours=2)),
        SimpleNamespace(id="old-1", create_time=now - timedelta(hours=25)),
        SimpleNamespace(id="old-2", create_time=now - timedelta(hours=26)),
        SimpleNamespace(id="old-3", create_time=now - timedelta(hours=27)),
        SimpleNamespace(id="old-4", create_time=now - timedelta(hours=28)),
        SimpleNamespace(id="old-5", create_time=now - timedelta(hours=29)),
        SimpleNamespace(id="recent-after-stop", create_time=now - timedelta(minutes=30)),
    ]
    api = _FakeApi(videos)
    client = TikTokClient(db=None)

    async def fake_create_api():
        return api

    client._create_api = fake_create_api

    recent_videos = await client.get_user_videos("vtv24news", max_count=10, since=now - timedelta(hours=24))

    assert [video.id for video in recent_videos] == ["recent-1", "recent-2"]


@pytest.mark.asyncio
async def test_get_user_videos_uses_create_time_from_as_dict_when_attribute_is_missing():
    now = datetime(2026, 1, 2, 12, 0, 0)

    videos = [
        SimpleNamespace(id="1", as_dict={"createTime": _timestamp(now - timedelta(hours=1))}),
        SimpleNamespace(id="2", as_dict={"createTime": _timestamp(now - timedelta(hours=25))}),
        SimpleNamespace(id="3", as_dict={"createTime": _timestamp(now - timedelta(hours=26))}),
        SimpleNamespace(id="4", as_dict={"createTime": _timestamp(now - timedelta(hours=27))}),
        SimpleNamespace(id="5", as_dict={"createTime": _timestamp(now - timedelta(hours=28))}),
        SimpleNamespace(id="6", as_dict={"createTime": _timestamp(now - timedelta(hours=29))}),
    ]
    api = _FakeApi(videos)
    client = TikTokClient(db=None)

    async def fake_create_api():
        return api

    client._create_api = fake_create_api

    recent_videos = await client.get_user_videos("vtv24news", max_count=10, since=now - timedelta(hours=24))

    assert [video.id for video in recent_videos] == ["1"]


@pytest.mark.asyncio
async def test_get_user_videos_excludes_videos_at_since_boundary():
    now = datetime(2026, 1, 2, 12, 0, 0)
    cutoff = now - timedelta(hours=2)
    videos = [
        SimpleNamespace(id="new", create_time=cutoff + timedelta(minutes=1)),
        SimpleNamespace(id="at-cutoff", create_time=cutoff),
        SimpleNamespace(id="old", create_time=cutoff - timedelta(minutes=1)),
    ]
    api = _FakeApi(videos)
    client = TikTokClient(db=None)

    async def fake_create_api():
        return api

    client._create_api = fake_create_api

    recent_videos = await client.get_user_videos("vtv24news", max_count=10, since=cutoff)

    assert [video.id for video in recent_videos] == ["new"]


@pytest.mark.asyncio
async def test_get_hashtag_videos_uses_hashtag_feed_and_limits_raw_results():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    videos = [
        _hashtag_video("1", now - timedelta(hours=1), {"playCount": "100"}),
        _hashtag_video("2", now - timedelta(hours=1), {"playCount": "200"}),
        _hashtag_video("ignored-by-max-count", now - timedelta(hours=1), {"playCount": "999999"}),
    ]
    api = _FakeHashtagApi(videos)
    client = TikTokClient(db=None)

    async def fake_create_api():
        return api

    client._create_api = fake_create_api

    hashtag_videos = await client.get_hashtag_videos("vtv24h", max_count=2)

    assert [video.id for video in hashtag_videos] == ["2", "1"]
    assert api.hashtag_names == ["vtv24h"]
    assert api.calls == [2]
    assert api.closed is True


@pytest.mark.asyncio
async def test_get_hashtag_videos_filters_last_24_hours_and_missing_create_time():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    videos = [
        _hashtag_video("recent", now - timedelta(hours=1), {"playCount": 10}),
        _hashtag_video("old", now - timedelta(hours=25), {"playCount": 999}),
        _hashtag_video("missing-create-time", None, {"playCount": 999}),
    ]
    api = _FakeHashtagApi(videos)
    client = TikTokClient(db=None)

    async def fake_create_api():
        return api

    client._create_api = fake_create_api

    hashtag_videos = await client.get_hashtag_videos("vtv24h", max_count=30)

    assert [video.id for video in hashtag_videos] == ["recent"]


@pytest.mark.asyncio
async def test_get_hashtag_videos_sorts_by_interaction_score_from_stats_and_stats_v2():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    videos = [
        _hashtag_video(
            "views-only",
            now - timedelta(hours=1),
            {"playCount": "100", "diggCount": "0", "commentCount": "0", "shareCount": "0", "collectCount": "0"},
        ),
        _hashtag_video(
            "stats-v2-engagement",
            now - timedelta(hours=2),
            {"playCount": "1", "diggCount": "10", "commentCount": "3", "shareCount": "2", "collectCount": "1"},
            stats_key="statsV2",
        ),
        _hashtag_video(
            "shares-win",
            now - timedelta(hours=3),
            {"playCount": "10", "diggCount": "0", "commentCount": "0", "shareCount": "20", "collectCount": "0"},
        ),
    ]
    api = _FakeHashtagApi(videos)
    client = TikTokClient(db=None)

    async def fake_create_api():
        return api

    client._create_api = fake_create_api

    hashtag_videos = await client.get_hashtag_videos("vtv24h", max_count=30)

    assert [video.id for video in hashtag_videos] == ["shares-win", "stats-v2-engagement", "views-only"]


@pytest.mark.asyncio
async def test_get_hashtag_videos_returns_top_15_when_more_recent_videos_match():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    videos = [
        _hashtag_video(str(index), now - timedelta(hours=1), {"playCount": index})
        for index in range(20)
    ]
    api = _FakeHashtagApi(videos)
    client = TikTokClient(db=None)

    async def fake_create_api():
        return api

    client._create_api = fake_create_api

    hashtag_videos = await client.get_hashtag_videos("vtv24h", max_count=30)

    assert [video.id for video in hashtag_videos] == [str(index) for index in range(19, 4, -1)]


@pytest.mark.asyncio
async def test_get_hashtag_videos_returns_all_matches_when_fewer_than_15_are_recent():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    videos = [
        _hashtag_video("1", now - timedelta(hours=1), {"playCount": 10}),
        _hashtag_video("2", now - timedelta(hours=2), {"playCount": 20}),
    ]
    api = _FakeHashtagApi(videos)
    client = TikTokClient(db=None)

    async def fake_create_api():
        return api

    client._create_api = fake_create_api

    hashtag_videos = await client.get_hashtag_videos("vtv24h", max_count=30)

    assert [video.id for video in hashtag_videos] == ["2", "1"]


@pytest.mark.asyncio
async def test_get_keyword_videos_uses_search_items_and_limits_raw_results():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    videos = [
        _keyword_video("1", now - timedelta(hours=1), {"playCount": "100"}),
        _keyword_video("2", now - timedelta(hours=1), {"playCount": "200"}),
        _keyword_video("ignored-by-max-count", now - timedelta(hours=1), {"playCount": "999999"}),
    ]
    api = _FakeSearchApi(videos)
    client = TikTokClient(db=None)

    async def fake_create_api():
        return api

    client._create_api = fake_create_api

    keyword_videos = await client.get_keyword_videos("doreamon", max_count=2)

    assert [video.id for video in keyword_videos] == ["2", "1"]
    assert api.calls == [("doreamon", "item")]
    assert api.closed is True


@pytest.mark.asyncio
async def test_get_keyword_videos_filters_last_24_hours_and_missing_create_time():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    videos = [
        _keyword_video("recent", now - timedelta(hours=1), {"playCount": 10}),
        _keyword_video("old", now - timedelta(hours=25), {"playCount": 999}),
        _keyword_video("missing-create-time", None, {"playCount": 999}),
    ]
    api = _FakeSearchApi(videos)
    client = TikTokClient(db=None)

    async def fake_create_api():
        return api

    client._create_api = fake_create_api

    keyword_videos = await client.get_keyword_videos("doreamon", max_count=30)

    assert [video.id for video in keyword_videos] == ["recent"]


@pytest.mark.asyncio
async def test_get_keyword_videos_sorts_by_interaction_score_from_stats_and_stats_v2():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    videos = [
        _keyword_video(
            "views-only",
            now - timedelta(hours=1),
            {"playCount": "100", "diggCount": "0", "commentCount": "0", "shareCount": "0", "collectCount": "0"},
        ),
        _keyword_video(
            "stats-v2-engagement",
            now - timedelta(hours=2),
            {"playCount": "1", "diggCount": "10", "commentCount": "3", "shareCount": "2", "collectCount": "1"},
            stats_key="statsV2",
        ),
        _keyword_video(
            "shares-win",
            now - timedelta(hours=3),
            {"playCount": "10", "diggCount": "0", "commentCount": "0", "shareCount": "20", "collectCount": "0"},
        ),
    ]
    api = _FakeSearchApi(videos)
    client = TikTokClient(db=None)

    async def fake_create_api():
        return api

    client._create_api = fake_create_api

    keyword_videos = await client.get_keyword_videos("doreamon", max_count=30)

    assert [video.id for video in keyword_videos] == ["shares-win", "stats-v2-engagement", "views-only"]


@pytest.mark.asyncio
async def test_get_keyword_videos_returns_top_15_when_more_recent_videos_match():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    videos = [
        _keyword_video(str(index), now - timedelta(hours=1), {"playCount": index})
        for index in range(20)
    ]
    api = _FakeSearchApi(videos)
    client = TikTokClient(db=None)

    async def fake_create_api():
        return api

    client._create_api = fake_create_api

    keyword_videos = await client.get_keyword_videos("doreamon", max_count=30)

    assert [video.id for video in keyword_videos] == [str(index) for index in range(19, 4, -1)]


@pytest.mark.asyncio
async def test_get_keyword_videos_returns_all_matches_when_fewer_than_15_are_recent():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    videos = [
        _keyword_video("1", now - timedelta(hours=1), {"playCount": 10}),
        _keyword_video("2", now - timedelta(hours=2), {"playCount": 20}),
    ]
    api = _FakeSearchApi(videos)
    client = TikTokClient(db=None)

    async def fake_create_api():
        return api

    client._create_api = fake_create_api

    keyword_videos = await client.get_keyword_videos("doreamon", max_count=30)

    assert [video.id for video in keyword_videos] == ["2", "1"]


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
        "override_browser_args": ["--mute-audio"],
        "sleep_after": 5,
        "ms_tokens": ["token-123"],
    }
