from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import TikTokSession


class TikTokClient:
    MAX_CONSECUTIVE_OLD_USER_VIDEOS = 5

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

    async def get_user_videos(
        self,
        username: str,
        max_count: int,
        since: datetime | None = None,
    ) -> list[Any]:
        api = await self._create_api()
        try:
            videos = []
            cutoff_time = (
                self._comparable_datetime(since)
                if since is not None
                else (datetime.now(timezone.utc) - timedelta(hours=24)).replace(tzinfo=None)
            )
            consecutive_old = 0
            async for video in api.user(username=username).videos(count=max_count):
                create_time = self.video_create_time(video)
                if cutoff_time and create_time:
                    if create_time < cutoff_time:
                        if self.video_is_pinned(video):
                            continue

                        consecutive_old += 1
                        if consecutive_old >= self.MAX_CONSECUTIVE_OLD_USER_VIDEOS:
                            break
                        continue

                consecutive_old = 0

                videos.append(video)
                if len(videos) >= max_count:
                    break
            return videos
        finally:
            await api.close_sessions()

    async def get_user_follower_count(self, username: str) -> int | None:
        api = await self._create_api()
        try:
            info = await api.user(username=username).info()
        finally:
            await api.close_sessions()

        stats = info.get("userInfo", {}).get("stats", {})
        follower_count = stats.get("followerCount")
        return int(follower_count) if follower_count is not None else None

    async def get_hashtag_videos(self, hashtag_name: str, max_count: int) -> list[Any]:
        api = await self._create_api()
        try:
            videos = []
            async for video in api.hashtag(name=hashtag_name).videos(count=max_count):
                videos.append(video)
                if len(videos) >= max_count:
                    break
            return videos
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
