from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import PipelineJob, PipelineLog, Post, Source
from app.services.tiktok_client import TikTokClient


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_nested(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _video_url(video: Any, data: dict[str, Any]) -> str:
    if data.get("webVideoUrl"):
        return data["webVideoUrl"]
    author = data.get("author")
    username = author if isinstance(author, str) else _get_nested(data, "author", "uniqueId")
    if username and getattr(video, "id", None):
        return f"https://www.tiktok.com/@{username}/video/{video.id}"
    if getattr(video, "id", None):
        return f"https://www.tiktok.com/video/{video.id}"
    raise ValueError("Video khong co id hop le")


def _video_to_post(source_id: int, video: Any) -> Post:
    data = getattr(video, "as_dict", {}) or {}
    video_data = data.get("video") or {}
    return Post(
        source_id=source_id,
        tiktok_video_id=str(getattr(video, "id", data.get("id"))),
        tiktok_url=_video_url(video, data),
        description=data.get("desc"),
        duration_seconds=_to_int(video_data.get("duration")),
        cover_url=video_data.get("cover") or video_data.get("originCover"),
        posted_at=getattr(video, "create_time", None) or _now(),
        created_at=_now(),
        is_tracked=True,
        is_deleted=False,
        metric_tier="bootstrap",
        cold_check_count=0,
        metric_scan_miss_count=0,
    )


def add_job_log(
    db: Session,
    job: PipelineJob,
    message: str,
    log_level: str = "INFO",
    error_type: str | None = None,
    error_details: str | None = None,
) -> None:
    db.add(
        PipelineLog(
            job_id=job.id,
            source_id=job.source_id,
            log_level=log_level,
            message=message,
            error_type=error_type,
            error_details=error_details,
            created_at=_now(),
        )
    )


async def crawl_source(db: Session, source: Source, max_count: int = 30) -> PipelineJob:
    client = TikTokClient(db)
    session_record = client.get_session_record()
    job = PipelineJob(
        job_type="scraper_job",
        source_id=source.id,
        session_id=session_record.id if session_record else None,
        status="running",
        started_at=_now(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        if source.source_type == "user":
            videos = await client.get_user_videos(source.identifier, max_count)
        elif source.source_type == "hashtag":
            videos = await client.get_hashtag_videos(source.identifier, max_count)
        else:
            # TODO: bo sung crawler cho sound/keyword.
            raise ValueError(f"Chua ho tro crawl source_type={source.source_type}")

        posts_new = 0
        for video in videos:
            tiktok_video_id = str(getattr(video, "id", None))
            if not tiktok_video_id:
                job.items_failed += 1
                continue

            exists = db.query(Post).filter(Post.tiktok_video_id == tiktok_video_id).first()
            if exists:
                continue

            db.add(_video_to_post(source.id, video))
            posts_new += 1

        job.posts_found = len(videos)
        job.posts_new = posts_new
        job.items_total = len(videos)
        job.items_updated = posts_new
        job.status = "done"
        job.finished_at = _now()
        source.last_scraped = job.finished_at
        add_job_log(db, job, f"Crawl xong: found={len(videos)}, new={posts_new}")
        # TODO: tinh next_scrape theo tier/schedule_override_minutes.
        db.commit()
    except Exception as exc:
        job.status = "failed"
        job.error_message = str(exc)
        job.items_failed = max(job.items_failed, 1)
        job.finished_at = _now()
        add_job_log(db, job, "Crawl source that bai", "ERROR", type(exc).__name__, str(exc))
        db.commit()

    db.refresh(job)
    return job
