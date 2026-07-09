from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import TikTokSession


class TikTokClient:
    MAX_CONSECUTIVE_OLD_USER_VIDEOS = 5
    MAX_TOP_RECENT_VIDEOS = 15

    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()

    def get_session_record(self) -> TikTokSession | None:
        return (
            self.db.query(TikTokSession)
            .filter(TikTokSession.is_active.is_(True), TikTokSession.is_valid.is_(True))
            .order_by(desc(TikTokSession.expires_at))
            .first()
        )

    def _get_ms_token(self) -> str | None:
        session = self.get_session_record()
        if session:
            return session.ms_token
        if self.settings.ms_token:
            return self.settings.ms_token
        return None

    async def _create_api(self) -> Any:
        from TikTokApi import TikTokApi

        ms_token = self._get_ms_token()
        api = TikTokApi()
        session_kwargs = {
            "num_sessions": 1,
            "headless": self.settings.tiktok_headless,
            "browser": self.settings.tiktok_browser,
            "override_browser_args": ["--mute-audio"],
            "sleep_after": self.settings.tiktok_sleep_after,
        }
        if ms_token:
            session_kwargs["ms_tokens"] = [ms_token]
        await api.create_sessions(**session_kwargs)
        return api

    @staticmethod
    def _comparable_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    @classmethod
    def video_create_time(cls, video: Any) -> datetime | None:
        data = getattr(video, "as_dict", {}) or {}
        raw = data.get("createTime") or data.get("create_time")

        if raw is not None:
            try:
                timestamp = float(raw)
                if timestamp > 9_999_999_999:
                    timestamp /= 1000
                return datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None)
            except (TypeError, ValueError):
                pass
        create_time = getattr(video, "create_time", None)
        if isinstance(create_time, datetime):
            return cls._comparable_datetime(create_time)

        return None

    @staticmethod
    def video_is_pinned(video: Any) -> bool:
        data = getattr(video, "as_dict", {}) or {}
        return bool(data.get("isPinnedPost") or data.get("isPinned"))

    @staticmethod
    def _video_stats(video: Any) -> dict[str, Any]:
        data = getattr(video, "as_dict", {}) or {}
        stats = data.get("statsV2") or data.get("stats") or getattr(video, "stats", None)
        return stats if isinstance(stats, dict) else {}

    @staticmethod
    def _stat_int(stats: dict[str, Any], key: str) -> int:
        try:
            return int(stats.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def video_interaction_score(cls, video: Any) -> int:
        stats = cls._video_stats(video)
        views = cls._stat_int(stats, "playCount")
        likes = cls._stat_int(stats, "diggCount")
        comments = cls._stat_int(stats, "commentCount")
        shares = cls._stat_int(stats, "shareCount")
        collect = cls._stat_int(stats, "collectCount")
        return views + likes * 5 + comments * 10 + shares * 8 + collect * 7

    @classmethod
    def _filter_recent_top_videos(cls, videos: list[Any]) -> list[Any]:
        cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(tzinfo=None)
        recent_videos = []
        for video in videos:
            create_time = cls.video_create_time(video)
            if create_time is None or create_time < cutoff_time:
                continue
            recent_videos.append(video)

        return sorted(recent_videos, key=cls.video_interaction_score, reverse=True)[
            : cls.MAX_TOP_RECENT_VIDEOS
        ]

    @staticmethod
    def _normalize_yt_dlp_video(item: dict[str, Any]) -> Any:
        video_id = item.get("id")
        url = item.get("webpage_url") or item.get("url")
        description = item.get("description") or item.get("title")
        uploader = item.get("uploader")
        thumbnail = item.get("thumbnail")
        duration = item.get("duration")
        timestamp = item.get("timestamp")
        share_count = item.get("repost_count")
        if share_count is None:
            share_count = item.get("share_count")

        return SimpleNamespace(
            id=str(video_id) if video_id else None,
            as_dict={
                "id": str(video_id) if video_id else None,
                "webVideoUrl": url,
                "desc": description,
                "createTime": timestamp,
                "author": {"uniqueId": uploader} if uploader else None,
                "video": {
                    "duration": duration,
                    "cover": thumbnail,
                    "originCover": thumbnail,
                },
                "statsV2": {
                    "diggCount": item.get("like_count"),
                    "shareCount": share_count,
                    "commentCount": item.get("comment_count"),
                    "playCount": item.get("view_count"),
                    "collectCount": item.get("save_count"),
                },
            },
        )

    @staticmethod
    def _yt_dlp_options(max_count: int) -> dict[str, Any]:
        settings = get_settings()
        options: dict[str, Any] = {
            "quiet": True,
            "skip_download": True,
            "extract_flat": False,
            "playlistend": max_count,
            "ignoreerrors": True,
            "noplaylist": False,
            "no_warnings": True,
        }
        if settings.ytdlp_request_delay_seconds > 0:
            options["sleep_interval_requests"] = settings.ytdlp_request_delay_seconds
        if settings.ytdlp_extractor_retries > 0:
            options["extractor_retries"] = settings.ytdlp_extractor_retries
        if settings.ytdlp_proxy_url:
            options["proxy"] = settings.ytdlp_proxy_url
        if settings.ytdlp_cookie_file:
            options["cookiefile"] = settings.ytdlp_cookie_file
        return options

    @staticmethod
    def _yt_dlp_identifier(entries: list[dict[str, Any] | None]) -> str | None:
        for item in entries:
            if not item:
                continue
            uploader = item.get("uploader")
            if uploader:
                return str(uploader).lstrip("@")
        return None

    @classmethod
    def _normalize_yt_dlp_entries(
        cls,
        entries: list[dict[str, Any] | None],
        max_count: int,
        since: datetime | None = None,
    ) -> list[Any]:
        cutoff_time = (
            cls._comparable_datetime(since)
            if since is not None
            else (datetime.now(timezone.utc) - timedelta(hours=24)).replace(tzinfo=None)
        )
        videos = []
        for item in entries:
            if not item:
                continue

            timestamp = item.get("timestamp")
            if timestamp is None:
                continue

            create_time = datetime.fromtimestamp(float(timestamp), timezone.utc).replace(tzinfo=None)
            if create_time <= cutoff_time:
                break

            videos.append(cls._normalize_yt_dlp_video(item))
            if len(videos) >= max_count:
                break

        return videos

    async def get_user_profile_videos(
        self,
        profile_url: str,
        max_count: int,
        since: datetime | None = None,
    ) -> tuple[str | None, list[Any]]:
        from yt_dlp import YoutubeDL

        with YoutubeDL(self._yt_dlp_options(max_count)) as ydl:
            result = ydl.extract_info(profile_url, download=False) or {}

        entries = result.get("entries") or []
        return (
            self._yt_dlp_identifier(entries),
            self._normalize_yt_dlp_entries(entries, max_count, since),
        )

    async def get_user_videos_by_url(
        self,
        profile_url: str,
        max_count: int,
        since: datetime | None = None,
    ) -> list[Any]:
        _, videos = await self.get_user_profile_videos(profile_url, max_count, since)
        return videos

    async def get_user_videos(
        self,
        username: str,
        max_count: int,
        since: datetime | None = None,
    ) -> list[Any]:
        profile_url = f"https://www.tiktok.com/@{username.lstrip('@')}"
        return await self.get_user_videos_by_url(profile_url, max_count, since)

    async def get_hashtag_videos(self, hashtag_name: str, max_count: int) -> list[Any]:
        api = await self._create_api()
        try:
            videos = []
            async for video in api.hashtag(name=hashtag_name).videos(count=max_count):
                videos.append(video)
                if len(videos) >= max_count:
                    break
            return self._filter_recent_top_videos(videos)
        finally:
            await api.close_sessions()

    async def get_keyword_videos(self, keyword: str, max_count: int) -> list[Any]:
        api = await self._create_api()
        try:
            videos = []
            async for video in api.search.search_type(keyword, "item"):
                videos.append(video)
                if len(videos) >= max_count:
                    break
            return self._filter_recent_top_videos(videos)
        finally:
            await api.close_sessions()

    async def get_video_comments(self, video_id: str, max_count: int) -> list[Any]:
        api = await self._create_api()
        try:
            comments = []
            async for comment in api.video(id=video_id).comments(count=max_count):
                comments.append(comment)
                if len(comments) >= max_count:
                    break
            return comments
        finally:
            await api.close_sessions()

    async def get_video_info(self, video_url: str) -> dict[str, Any]:
        api = await self._create_api()
        try:
            return await api.video(url=video_url).info()
        finally:
            await api.close_sessions()
