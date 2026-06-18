import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database import SessionLocal
from app.models import Post, Source
from app.services.metric_service import update_post_metric
from app.services.scraper_service import crawl_source


SUPPORTED_SOURCE_TYPES = ("user", "hashtag", "keyword")


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

    source_jobs = []
    for source in due_sources(db, current_time, source_batch_size):
        job = await crawl_source(db, source, max_count=max_count)
        source_jobs.append(job.id)

    post_jobs = []
    for post in due_posts(db, current_time, post_batch_size):
        job = await update_post_metric(db, post)
        post_jobs.append(job.id)

    return {
        "sources_processed": len(source_jobs),
        "posts_processed": len(post_jobs),
        "source_job_ids": source_jobs,
        "post_job_ids": post_jobs,
    }


async def run_scheduler_forever() -> None:
    settings = get_settings()
    while True:
        await asyncio.sleep(settings.scheduler_interval_seconds)
        db = SessionLocal()
        try:
            await run_scheduler_cycle(db)
        finally:
            db.close()
