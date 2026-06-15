from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session
from TikTokApi import TikTokApi

from app.core.config import get_settings
from app.models import TikTokSession


class TikTokClient:
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

    def _get_ms_token(self) -> str:
        session = self.get_session_record()
        if session:
            return session.ms_token
        if self.settings.ms_token:
            return self.settings.ms_token
        raise RuntimeError("Khong tim thay ms_token hop le trong DB hoac .env")

    async def _create_api(self) -> TikTokApi:
        ms_token = self._get_ms_token()
        api = TikTokApi()
        await api.create_sessions(
            num_sessions=1,
            ms_tokens=[ms_token],
            headless=self.settings.tiktok_headless,
        )
        return api

    async def get_user_videos(self, username: str, max_count: int) -> list[Any]:
        api = await self._create_api()
        try:
            videos = []
            async for video in api.user(username=username).videos(count=max_count):
                videos.append(video)
                if len(videos) >= max_count:
                    break
            return videos
        finally:
            await api.close_sessions()

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
