from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import PipelineJob, Post, PostMetric
from app.services.scraper_service import add_job_log
from app.services.tiktok_client import TikTokClient


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _stats_from_info(info: dict[str, Any]) -> dict[str, Any]:
    return info.get("statsV2") or info.get("stats") or {}


async def update_post_metric(db: Session, post: Post) -> PipelineJob:
    client = TikTokClient(db)
    session_record = client.get_session_record()
    job = PipelineJob(
        job_type="update_metric",
        source_id=post.source_id,
        session_id=session_record.id if session_record else None,
        status="running",
        items_total=1,
        started_at=_now(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        info = await client.get_video_info(post.tiktok_url)
        stats = _stats_from_info(info)
        recorded_at = _now()
        db.add(
            PostMetric(
                post_id=post.id,
                likes_count=_to_int(stats.get("diggCount")),
                shares_count=_to_int(stats.get("shareCount")),
                comments_count=_to_int(stats.get("commentCount")),
                views_count=_to_int(stats.get("playCount")),
                bookmarks_count=_to_int(stats.get("collectCount")),
                recorded_at=recorded_at,
                job_id=job.id,
            )
        )
        post.last_metric_update = recorded_at
        job.items_updated = 1
        job.status = "done"
        job.finished_at = recorded_at
        add_job_log(db, job, "Update metric xong")
        # TODO: cap nhat metric_tier, velocity, next_metric_update.
        db.commit()
    except Exception as exc:
        job.status = "failed"
        job.items_failed = 1
        job.error_message = str(exc)
        job.finished_at = _now()
        add_job_log(db, job, "Update metric that bai", "ERROR", type(exc).__name__, str(exc))
        db.commit()

    db.refresh(job)
    return job
