import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import Hashtag, PipelineJob, PipelineLog, Post, PostMetric, Source, TaskLog
from app.services.tier_service import metric_tier_from_metric, next_metric_update_at, refresh_source_schedule
from app.services.tiktok_client import TikTokClient


_HASHTAG_RE = re.compile(r"(?<!\w)#(\w+)", re.UNICODE)
logger = logging.getLogger("tiktok_api.scraper")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _user_video_cutoff(source: Source | None = None) -> datetime:
    max_days_old = source.max_days_old if source and source.max_days_old is not None else 1
    return _now() - timedelta(days=max(max_days_old, 1))


def _user_video_since(db: Session, source: Source, latest_posted_at: datetime | None = None) -> datetime:
    cutoff = _user_video_cutoff(source)
    if latest_posted_at is None:
        latest_posted_at = _latest_posted_at_for_source(db, source)
    if latest_posted_at is None:
        return cutoff
    return max(cutoff, latest_posted_at)


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


def _extract_hashtags(description: str | None) -> list[str]:
    if not description:
        return []

    tags = []
    seen = set()
    for match in _HASHTAG_RE.finditer(description):
        tag = match.group(1).lower()
        if tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def _attach_post_hashtags(db: Session, post: Post) -> None:
    tags = _extract_hashtags(post.description)
    if not tags:
        return

    existing = db.query(Hashtag).filter(Hashtag.tag.in_(tags)).all()
    hashtags_by_tag = {hashtag.tag: hashtag for hashtag in existing}

    for tag in tags:
        hashtag = hashtags_by_tag.get(tag)
        if hashtag is None:
            hashtag = Hashtag(tag=tag)
            db.add(hashtag)
            hashtags_by_tag[tag] = hashtag
        post.hashtags.append(hashtag)


def _video_id(video: Any, data: dict[str, Any] | None = None) -> str | None:
    if data is None:
        data = getattr(video, "as_dict", {}) or {}
    video_id = getattr(video, "id", None) or data.get("id")
    return str(video_id) if video_id else None


def _video_stats(video: Any, data: dict[str, Any] | None = None) -> dict[str, Any]:
    if data is None:
        data = getattr(video, "as_dict", {}) or {}
    stats = data.get("statsV2") or data.get("stats") or getattr(video, "stats", None)
    return stats if isinstance(stats, dict) else {}


def _video_url(video: Any, data: dict[str, Any]) -> str:
    if data.get("webVideoUrl"):
        return data["webVideoUrl"]
    author = data.get("author")
    username = author if isinstance(author, str) else _get_nested(data, "author", "uniqueId")
    video_id = _video_id(video, data)
    if username and video_id:
        return f"https://www.tiktok.com/@{username}/video/{video_id}"
    if video_id:
        return f"https://www.tiktok.com/video/{video_id}"
    raise ValueError("Video khong co id hop le")


def _video_to_post(source_id: int, video: Any) -> Post:
    data = getattr(video, "as_dict", {}) or {}
    video_data = data.get("video") or {}
    create_time = TikTokClient.video_create_time(video)
    return Post(
        source_id=source_id,
        tiktok_video_id=_video_id(video, data),
        tiktok_url=_video_url(video, data),
        description=data.get("desc"),
        duration_seconds=_to_int(video_data.get("duration")),
        cover_url=video_data.get("cover") or video_data.get("originCover"),
        posted_at=create_time or _now(),
        created_at=_now(),
        is_tracked=True,
        is_deleted=False,
        metric_tier="bootstrap",
        cold_check_count=0,
        metric_scan_miss_count=0,
    )


def _latest_posted_at_for_source(db: Session, source: Source) -> datetime | None:
    return (
        db.query(Post.posted_at)
        .filter(Post.source_id == source.id)
        .order_by(Post.posted_at.desc())
        .limit(1)
        .scalar()
    )


def _video_to_metric(post: Post, video: Any, job_id: int, recorded_at: datetime) -> PostMetric | None:
    data = getattr(video, "as_dict", {}) or {}
    stats = _video_stats(video, data)
    if not stats:
        return None
    return PostMetric(
        post_id=post.id,
        likes_count=_to_int(stats.get("diggCount")),
        shares_count=_to_int(stats.get("shareCount")),
        comments_count=_to_int(stats.get("commentCount")),
        views_count=_to_int(stats.get("playCount")),
        bookmarks_count=_to_int(stats.get("collectCount")),
        recorded_at=recorded_at,
        job_id=job_id,
    )


def add_job_log(
    db: Session,
    job: PipelineJob,
    message: str,
    log_level: str = "ERROR",
    error_type: str | None = None,
    error_details: str | None = None,
) -> None:
    if log_level.upper() != "ERROR":
        return

    db.add(
        PipelineLog(
            job_id=job.id,
            source_id=job.source_id,
            log_level="ERROR",
            message=message,
            error_type=error_type,
            error_details=error_details,
            created_at=_now(),
        )
    )


def add_task_log(db: Session, job: PipelineJob) -> None:
    completed_at = job.finished_at or _now()
    started_at = job.started_at or completed_at
    task_names = {
        "scrape_24h": "scrape_posts",
        "scraper_job": "scrape_posts",
        "update_metric": "update_metrics",
        "analytics": "generate_analytics",
    }
    db.add(
        TaskLog(
            task_name=task_names.get(job.job_type, job.job_type),
            status=job.status,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=(completed_at - started_at).total_seconds(),
            items_processed=job.items_total,
            errors_count=job.items_failed,
            error_message=job.error_message,
            created_at=_now(),
        )
    )


def _create_scraper_job(db: Session, source: Source) -> PipelineJob:
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
    return job


def _complete_crawl_job(
    db: Session,
    source: Source,
    job: PipelineJob,
    videos: list[Any],
    latest_posted_at: datetime | None = None,
) -> None:
    posts_new = 0
    items_total = 0
    for video in videos:
        tiktok_video_id = _video_id(video)
        if not tiktok_video_id:
            items_total += 1
            job.items_failed += 1
            continue

        create_time = TikTokClient.video_create_time(video)
        if latest_posted_at is not None and create_time is not None and create_time <= latest_posted_at:
            break

        items_total += 1
        exists = db.query(Post).filter(Post.tiktok_video_id == tiktok_video_id).first()
        if exists:
            continue

        post = _video_to_post(source.id, video)
        db.add(post)
        db.flush()
        _attach_post_hashtags(db, post)

        recorded_at = _now()
        metric = _video_to_metric(post, video, job.id, recorded_at)
        if metric:
            db.add(metric)
            post.last_metric_update = recorded_at
            post.metric_tier = metric_tier_from_metric(metric)
            post.next_metric_update = next_metric_update_at(recorded_at)
        posts_new += 1

    job.posts_found = items_total
    job.posts_new = posts_new
    job.items_total = items_total
    job.items_updated = posts_new
    job.status = "done"
    job.finished_at = _now()
    source.last_scraped = job.finished_at
    refresh_source_schedule(db, source, job.finished_at)
    add_task_log(db, job)
    db.commit()


def _fail_crawl_job(
    db: Session,
    source: Source,
    job: PipelineJob,
    exc: Exception,
) -> None:
    job.status = "failed"
    job.error_message = str(exc)
    job.items_failed = max(job.items_failed, 1)
    job.finished_at = _now()
    add_job_log(db, job, "Crawl source that bai", "ERROR", type(exc).__name__, str(exc))
    add_task_log(db, job)
    db.commit()


def crawl_source_with_videos(
    db: Session,
    source: Source,
    videos: list[Any],
    latest_posted_at: datetime | None = None,
) -> PipelineJob:
    source_name = source.display_name or source.identifier
    logger.info(
        "Bat dau luu bai moi da crawl | source=%s id=%s type=%s count=%s",
        source_name,
        source.id,
        source.source_type,
        len(videos),
    )
    job = _create_scraper_job(db, source)
    try:
        _complete_crawl_job(db, source, job, videos, latest_posted_at)
        logger.info(
            "Hoan tat luu bai moi da crawl | source=%s id=%s found=%s new=%s failed=%s",
            source_name,
            source.id,
            job.posts_found,
            job.posts_new,
            job.items_failed,
        )
    except Exception as exc:
        _fail_crawl_job(db, source, job, exc)
        logger.error(
            "Luu bai moi da crawl that bai | source=%s id=%s error=%s",
            source_name,
            source.id,
            exc,
        )

    db.refresh(job)
    return job


async def crawl_source(db: Session, source: Source, max_count: int = 30) -> PipelineJob:
    source_name = source.display_name or source.identifier
    logger.info(
        "Bat dau scrape bai moi | source=%s id=%s type=%s max_count=%s",
        source_name,
        source.id,
        source.source_type,
        max_count,
    )
    client = TikTokClient(db)
    job = _create_scraper_job(db, source)

    try:
        latest_posted_at = None
        if source.source_type == "user":
            latest_posted_at = _latest_posted_at_for_source(db, source)
            videos = await client.get_user_videos(
                source.identifier,
                max_count,
                since=_user_video_since(db, source, latest_posted_at),
            )
        elif source.source_type == "hashtag":
            videos = await client.get_hashtag_videos(source.identifier, max_count)
        elif source.source_type == "keyword":
            videos = await client.get_keyword_videos(source.identifier, max_count)
        else:
            # TODO: bo sung crawler cho sound.
            raise ValueError(f"Chua ho tro crawl source_type={source.source_type}")

        _complete_crawl_job(db, source, job, videos, latest_posted_at)
        logger.info(
            "Hoan tat scrape bai moi | source=%s id=%s found=%s new=%s failed=%s",
            source_name,
            source.id,
            job.posts_found,
            job.posts_new,
            job.items_failed,
        )
    except Exception as exc:
        _fail_crawl_job(db, source, job, exc)
        logger.error(
            "Scrape bai moi that bai | source=%s id=%s error=%s",
            source_name,
            source.id,
            exc,
        )

    db.refresh(job)
    return job
