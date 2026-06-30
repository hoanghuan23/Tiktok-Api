import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from bs4 import BeautifulSoup
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import PipelineJob, Post, PostMetric, Source, TikTokSession
from app.services.scraper_service import add_job_log, add_task_log
from app.services.tier_service import metric_tier_from_metric, next_metric_update_at
from app.services.tiktok_client import TikTokClient


logger = logging.getLogger("tiktok_api.metrics")


class DeletedTikTokVideoError(Exception):
    """Raised when TikTok/yt-dlp reports that a video is no longer accessible."""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_metrics_from_html(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")
    if tag is None or not tag.string:
        html_lower = html.lower()
        is_waf = "wafchallengeid" in html_lower or "please wait" in html_lower
        is_captcha = "captcha" in html_lower
        raise ValueError(
            "Khong co rehydration script. "
            f"waf={is_waf}, captcha={is_captcha}, html_len={len(html)}"
        )

    data = json.loads(tag.string)
    item = data["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
    stats = item.get("statsV2") or item.get("stats") or {}
    return {
        "video_id": item.get("id"),
        "author": item.get("author", {}).get("uniqueId"),
        "views_count": _to_int(stats.get("playCount")),
        "likes_count": _to_int(stats.get("diggCount")),
        "comments_count": _to_int(stats.get("commentCount")),
        "shares_count": _to_int(stats.get("shareCount")),
        "bookmarks_count": _to_int(stats.get("collectCount")),
    }


def _is_post_older_than_24h(post: Post, now: datetime) -> bool:
    posted_at = post.posted_at
    if posted_at.tzinfo is not None:
        posted_at = posted_at.astimezone(timezone.utc).replace(tzinfo=None)
    return posted_at <= now - timedelta(hours=24)


def _is_retryable_metric_error(error: str | None) -> bool:
    if not error:
        return False
    if _is_deleted_metric_error(error):
        return False
    error = error.lower()
    return any(
        marker in error
        for marker in ("captcha=true", "waf=true", "timeout", "connection", "403", "forbidden")
    )


def _is_deleted_metric_error(error: str | None) -> bool:
    if not error:
        return False
    error = error.lower()
    return any(
        marker in error
        for marker in (
            "blocked from accessing this post",
            "couldn't find this post",
            "could not find this post",
            "video is currently unavailable",
            "this video is unavailable",
            "post is unavailable",
        )
    )


def _should_retry_metric_result(result: dict[str, Any]) -> bool:
    """Return whether a failed request is worth retrying with the same session."""
    if result.get("ok"):
        return False

    return (
        _is_retryable_metric_error(result.get("error"))
        or result.get("status_code") in {403, 408, 429, 500, 502, 503, 504}
    )


def due_posts_for_source(db: Session, source_id: int, now: datetime) -> list[Post]:
    return (
        db.query(Post)
        .filter(Post.source_id == source_id)
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
        .all()
    )


async def _fetch_one_metric(
    post: Post,
    worker_id: int,
    timeout: int,
) -> dict[str, Any]:
    try:
        metrics = await asyncio.to_thread(extract_tiktok_video_metrics, post.tiktok_url, timeout)
        if metrics is None:
            return {
                "post_id": post.id,
                "url": post.tiktok_url,
                "worker": worker_id,
                "ok": False,
                "error": "yt-dlp khong lay duoc metric",
            }
        return {
            "post_id": post.id,
            "url": post.tiktok_url,
            "worker": worker_id,
            "ok": True,
            "metrics": metrics,
        }
    except DeletedTikTokVideoError as exc:
        return {
            "post_id": post.id,
            "url": post.tiktok_url,
            "worker": worker_id,
            "ok": False,
            "is_deleted": True,
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "post_id": post.id,
            "url": post.tiktok_url,
            "worker": worker_id,
            "ok": False,
            "error": str(exc),
        }


def extract_tiktok_video_metrics(video_url: str, timeout: int | None = None) -> dict[str, Any] | None:
    from yt_dlp import YoutubeDL

    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": False,
        "ignoreerrors": False,
        "noplaylist": True,
        "no_warnings": True,
    }
    if timeout is not None:
        ydl_opts["socket_timeout"] = timeout

    try:
        with YoutubeDL(ydl_opts) as ydl:
            item = ydl.extract_info(video_url, download=False)
    except Exception as exc:
        if _is_deleted_metric_error(str(exc)):
            logger.info("TikTok video khong con truy cap duoc | url=%s error=%s", video_url, exc)
            raise DeletedTikTokVideoError(str(exc)) from exc
        logger.warning("yt-dlp khong lay duoc metric | url=%s error=%s", video_url, exc)
        return None

    if not item:
        return None

    share_count = item.get("repost_count")
    if share_count is None:
        share_count = item.get("share_count")

    return {
        "likes_count": _to_int(item.get("like_count")),
        "shares_count": _to_int(share_count),
        "comments_count": _to_int(item.get("comment_count")),
        "views_count": _to_int(item.get("view_count")),
        "bookmarks_count": _to_int(item.get("save_count")),
    }


async def _metric_worker(
    worker_id: int,
    queue: asyncio.Queue[Post],
    results: list[dict[str, Any]],
    _session_record: TikTokSession | None,
) -> None:
    settings = get_settings()
    has_made_request = False
    while True:
        try:
            post = queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        if has_made_request and settings.metric_request_delay_seconds > 0:
            await asyncio.sleep(settings.metric_request_delay_seconds)

        result = await _fetch_one_metric(
            post,
            worker_id,
            settings.metric_timeout_seconds,
        )
        has_made_request = True
        for _attempt in range(settings.metric_max_retries):
            if not _should_retry_metric_result(result):
                break
            await asyncio.sleep(settings.metric_retry_delay_seconds)
            result = await _fetch_one_metric(
                post,
                worker_id,
                settings.metric_timeout_seconds,
            )
        results.append(result)


async def _fetch_metric_results(
    posts: list[Post],
    session_record: TikTokSession | None,
) -> list[dict[str, Any]]:
    settings = get_settings()
    queue: asyncio.Queue[Post] = asyncio.Queue()
    for post in posts:
        await queue.put(post)

    results: list[dict[str, Any]] = []
    worker_count = max(1, min(settings.metric_num_workers, len(posts)))
    await asyncio.gather(
        *[
            asyncio.create_task(_metric_worker(index + 1, queue, results, session_record))
            for index in range(worker_count)
        ]
    )
    return results


def _metric_from_result(post: Post, result: dict[str, Any], recorded_at: datetime, job_id: int) -> PostMetric:
    metrics = result["metrics"]
    return PostMetric(
        post_id=post.id,
        likes_count=metrics.get("likes_count"),
        shares_count=metrics.get("shares_count"),
        comments_count=metrics.get("comments_count"),
        views_count=metrics.get("views_count"),
        bookmarks_count=metrics.get("bookmarks_count"),
        recorded_at=recorded_at,
        job_id=job_id,
    )


async def update_post_metric(db: Session, post: Post) -> PipelineJob:
    started_at = _now()
    should_skip = _is_post_older_than_24h(post, started_at)
    client = TikTokClient(db)
    session_record = client.get_session_record()
    job = PipelineJob(
        job_type="update_metric",
        source_id=post.source_id,
        session_id=session_record.id if session_record else None,
        status="running",
        items_total=0 if should_skip else 1,
        started_at=started_at,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    if should_skip:
        post.is_tracked = False
        job.status = "done"
        job.finished_at = _now()
        add_task_log(db, job)
        db.commit()
        db.refresh(job)
        return job

    try:
        metrics = await asyncio.to_thread(
            extract_tiktok_video_metrics,
            post.tiktok_url,
            get_settings().metric_timeout_seconds,
        )
        if metrics is None:
            raise ValueError("yt-dlp khong lay duoc metric")
        recorded_at = _now()
        metric = PostMetric(
            post_id=post.id,
            likes_count=metrics.get("likes_count"),
            shares_count=metrics.get("shares_count"),
            comments_count=metrics.get("comments_count"),
            views_count=metrics.get("views_count"),
            bookmarks_count=metrics.get("bookmarks_count"),
            recorded_at=recorded_at,
            job_id=job.id,
        )
        db.add(metric)
        post.last_metric_update = recorded_at
        post.metric_tier = metric_tier_from_metric(metric)
        post.next_metric_update = next_metric_update_at(recorded_at)
        job.items_updated = 1
        job.status = "done"
        job.finished_at = recorded_at
        add_task_log(db, job)
        db.commit()
    except DeletedTikTokVideoError:
        post.is_tracked = False
        post.is_deleted = True
        post.next_metric_update = None
        job.items_updated = 1
        job.status = "done"
        job.finished_at = _now()
        add_task_log(db, job)
        db.commit()
    except Exception as exc:
        job.status = "failed"
        job.items_failed = 1
        job.error_message = str(exc)
        job.finished_at = _now()
        add_job_log(db, job, "Update metric that bai", "ERROR", type(exc).__name__, str(exc))
        add_task_log(db, job)
        db.commit()

    db.refresh(job)
    return job


async def update_source_metrics(
    db: Session,
    source: Source,
    posts: list[Post] | None = None,
    now: datetime | None = None,
) -> PipelineJob:
    started_at = now or _now()
    source_name = source.display_name or source.identifier
    client = TikTokClient(db)
    session_record = client.get_session_record()
    update_posts = list(posts) if posts is not None else due_posts_for_source(db, source.id, started_at)
    active_posts: list[Post] = []
    skipped_old = 0
    for post in update_posts:
        if _is_post_older_than_24h(post, started_at):
            post.is_tracked = False
            skipped_old += 1
        else:
            active_posts.append(post)

    logger.info(
        "Bat dau cap nhat metrics | source=%s id=%s posts=%s skipped_old=%s",
        source_name,
        source.id,
        len(active_posts),
        skipped_old,
    )

    job = PipelineJob(
        job_type="update_metric",
        source_id=source.id,
        session_id=session_record.id if session_record else None,
        status="running",
        items_total=len(active_posts),
        items_failed=skipped_old,
        started_at=started_at,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    if not active_posts:
        job.status = "done"
        job.finished_at = _now()
        add_task_log(db, job)
        db.commit()
        db.refresh(job)
        logger.info("Bo qua cap nhat metrics | source=%s id=%s khong co post den han", source_name, source.id)
        return job

    results = await _fetch_metric_results(active_posts, session_record)
    posts_by_id = {post.id: post for post in active_posts}
    recorded_at = _now()
    failed_results = []

    for result in results:
        post = posts_by_id.get(result["post_id"])
        if post is None:
            continue
        if not result["ok"]:
            if result.get("is_deleted"):
                post.is_tracked = False
                post.is_deleted = True
                post.next_metric_update = None
                continue
            failed_results.append(result)
            continue

        metric = _metric_from_result(post, result, recorded_at, job.id)
        db.add(metric)
        post.last_metric_update = recorded_at
        post.metric_tier = metric_tier_from_metric(metric)
        post.next_metric_update = next_metric_update_at(recorded_at)

    job.items_updated = len(results) - len(failed_results)
    job.items_failed = skipped_old + len(failed_results)
    job.status = "done" if job.items_updated > 0 or job.items_failed < job.items_total else "failed"
    if failed_results:
        errors = [
            f"{result['post_id']}: {result.get('error', 'unknown error')}"
            for result in failed_results[:5]
        ]
        job.error_message = "; ".join(errors)
        for result in failed_results[:10]:
            add_job_log(
                db,
                job,
                "Update metric that bai",
                "ERROR",
                "MetricFetchError",
                f"post_id={result['post_id']} url={result['url']} error={result.get('error')}",
            )
    job.finished_at = recorded_at
    add_task_log(db, job)
    db.commit()
    db.refresh(job)
    logger.info(
        "Hoan tat cap nhat metrics | source=%s id=%s updated=%s failed=%s",
        source_name,
        source.id,
        job.items_updated,
        job.items_failed,
    )
    return job
