import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database import SessionLocal
from app.models import Post, Source
from app.services.metric_service import update_source_metrics
from app.services.scraper_service import crawl_source


SUPPORTED_SOURCE_TYPES = ("user", "hashtag", "keyword")
logger = logging.getLogger("tiktok_api.scheduler")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def due_sources(db: Session, now: datetime, limit: int | None = None) -> list[Source]:
    query = (
        db.query(Source)
        .filter(Source.source_type.in_(SUPPORTED_SOURCE_TYPES))
        .filter(Source.is_active.is_(True))
        .filter(or_(Source.is_accessible.is_(True), Source.is_accessible.is_(None)))
        .filter(or_(Source.next_scrape.is_(None), Source.next_scrape <= now))
        .order_by(Source.next_scrape.is_not(None), Source.next_scrape.asc(), Source.id.asc())
    )
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def due_posts(db: Session, now: datetime, limit: int | None = None) -> list[Post]:
    query = (
        db.query(Post)
        .filter(Post.posted_at > now - timedelta(hours=24))
        .filter(or_(Post.is_tracked.is_(True), Post.is_tracked.is_(None)))
        .filter(or_(Post.is_deleted.is_(False), Post.is_deleted.is_(None)))
        .filter(or_(Post.next_metric_update.is_(None), Post.next_metric_update <= now))
        .order_by(
            Post.next_metric_update.is_not(None),
            Post.next_metric_update.asc(),
            Post.last_metric_update.asc(),
            Post.id.asc(),
        )
    )
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def expire_old_tracked_posts(db: Session, now: datetime) -> int:
    expired_count = (
        db.query(Post)
        .filter(Post.posted_at <= now - timedelta(hours=24))
        .filter(or_(Post.is_tracked.is_(True), Post.is_tracked.is_(None)))
        .filter(or_(Post.is_deleted.is_(False), Post.is_deleted.is_(None)))
        .update({Post.is_tracked: False}, synchronize_session=False)
    )
    db.commit()
    return expired_count


async def run_scheduler_cycle(
    db: Session,
    now: datetime | None = None,
    source_limit: int | None = None,
    post_limit: int | None = None,
    max_count: int = 30,
) -> dict[str, Any]:
    settings = get_settings()
    current_time = now or _now()
    source_batch_size = source_limit if source_limit is not None else settings.scheduler_source_batch_size
    post_batch_size = post_limit if post_limit is not None else settings.scheduler_post_batch_size
    posts_expired = expire_old_tracked_posts(db, current_time)

    source_jobs = []
    due_source_batch = due_sources(db, current_time, source_batch_size)
    if due_source_batch:
        logger.info("Scheduler bat dau scrape bai moi | sources_due=%s", len(due_source_batch))
    for source in due_source_batch:
        job = await crawl_source(db, source, max_count=max_count)
        source_jobs.append(job.id)

    post_jobs = []
    posts_by_source: dict[int, list[Post]] = defaultdict(list)
    due_post_batch = due_posts(db, current_time, post_batch_size)
    for post in due_post_batch:
        posts_by_source[post.source_id].append(post)

    for source_id, posts in posts_by_source.items():
        source = db.get(Source, source_id)
        if source is None:
            continue
        job = await update_source_metrics(db, source, posts=posts, now=current_time)
        post_jobs.append(job.id)

    if source_jobs or post_jobs or posts_expired:
        logger.info(
            "Scheduler hoan tat chu ky | sources_processed=%s posts_processed=%s posts_expired=%s",
            len(source_jobs),
            len(due_post_batch),
            posts_expired,
        )

    return {
        "sources_processed": len(source_jobs),
        "posts_processed": len(due_post_batch),
        "posts_expired": posts_expired,
        "source_job_ids": source_jobs,
        "post_job_ids": post_jobs,
    }


async def run_scheduler_forever() -> None:
    settings = get_settings()
    logger.info("Scheduler da bat dau | interval_seconds=%s", settings.scheduler_interval_seconds)
    while True:
        await asyncio.sleep(settings.scheduler_interval_seconds)
        db = SessionLocal()
        try:
            await run_scheduler_cycle(db)
        finally:
            db.close()
