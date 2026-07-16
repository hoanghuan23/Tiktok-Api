import contextlib
import io
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings


class GalleryDLTikTokError(Exception):
    """Raised when gallery-dl cannot extract TikTok metadata."""


def _first_url(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            url = _first_url(item)
            if url:
                return url
    if isinstance(value, dict):
        return _first_url(value.get("urlList") or value.get("urls") or value.get("url"))
    return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_int(*values: Any) -> int | None:
    for value in values:
        parsed = _to_int(value)
        if parsed is not None:
            return parsed
    return None


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if value is None:
        return None

    timestamp = _to_int(value)
    if timestamp is None:
        return None
    if timestamp > 10_000_000_000:
        timestamp = timestamp // 1000
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)


def _author_username(post: dict[str, Any]) -> str | None:
    author = post.get("author") or post.get("user")
    if isinstance(author, str):
        return author.lstrip("@")
    if isinstance(author, dict):
        username = author.get("uniqueId") or author.get("unique_id") or author.get("nickname")
        return str(username).lstrip("@") if username else None
    return None


def _tiktok_url(post: dict[str, Any], fallback_url: str) -> str:
    if post.get("webVideoUrl"):
        return post["webVideoUrl"]
    if post.get("url"):
        return post["url"]

    post_id = post.get("id")
    username = _author_username(post)
    if post_id and username:
        post_kind = "photo" if post.get("imagePost") else "video"
        return f"https://www.tiktok.com/@{username}/{post_kind}/{post_id}"
    if post_id:
        return f"https://www.tiktok.com/video/{post_id}"
    return fallback_url


def _image_urls(post: dict[str, Any]) -> list[str]:
    image_post = post.get("imagePost", {})
    if not isinstance(image_post, dict):
        return []

    images = []
    for img in image_post.get("images", []):
        url = _first_url(img.get("imageURL") or img.get("imageUrl") or img) if isinstance(img, dict) else _first_url(img)
        if url:
            images.append(url)
    return images


def _post_metrics(post: dict[str, Any]) -> dict[str, int | None]:
    stats = post.get("stats") if isinstance(post.get("stats"), dict) else {}
    stats_v2 = post.get("statsV2") if isinstance(post.get("statsV2"), dict) else {}

    return {
        "likes_count": _first_int(
            stats_v2.get("diggCount"),
            stats.get("diggCount"),
            stats_v2.get("likeCount"),
            stats.get("likeCount"),
            post.get("like_count"),
            post.get("likes"),
        ),
        "shares_count": _first_int(
            stats_v2.get("shareCount"),
            stats.get("shareCount"),
            post.get("repost_count"),
            post.get("share_count"),
            post.get("shares"),
        ),
        "comments_count": _first_int(
            stats_v2.get("commentCount"),
            stats.get("commentCount"),
            post.get("comment_count"),
            post.get("comments"),
        ),
        "views_count": _first_int(
            stats_v2.get("playCount"),
            stats.get("playCount"),
            stats_v2.get("viewCount"),
            stats.get("viewCount"),
            post.get("view_count"),
            post.get("views"),
        ),
        "bookmarks_count": _first_int(
            stats_v2.get("collectCount"),
            stats.get("collectCount"),
            post.get("save_count"),
            post.get("bookmarks"),
        ),
    }


def _is_post_metadata(data: dict[str, Any]) -> bool:
    return bool(data.get("id") and (data.get("stats") or data.get("statsV2") or data.get("imagePost")))


def _build_result(post: dict[str, Any], requested_url: str) -> dict[str, Any]:
    video = post.get("video") if isinstance(post.get("video"), dict) else {}
    images = _image_urls(post)
    cover_url = (
        _first_url(video.get("cover"))
        or _first_url(video.get("originCover"))
        or _first_url(video.get("dynamicCover"))
        or (images[0] if images else None)
    )

    return {
        "tiktok_video_id": str(post["id"]) if post.get("id") else None,
        "tiktok_url": _tiktok_url(post, requested_url),
        "description": post.get("desc"),
        "duration_seconds": _to_int(video.get("duration")),
        "cover_url": cover_url,
        "posted_at": _to_datetime(post.get("date") or post.get("createTime") or post.get("create_time")),
        "author": _author_username(post),
        "metrics": _post_metrics(post),
    }


def extract_tiktok_posts(url: str, max_count: int | None = None) -> list[dict[str, Any]]:
    try:
        import gallery_dl.config as gdl_config
        import gallery_dl.job as gdl_job
    except Exception as exc:
        raise GalleryDLTikTokError(f"gallery-dl khong kha dung: {exc}") from exc

    gdl_config.set(("extractor", "tiktok"), "photos", False)
    gdl_config.set(("extractor", "tiktok"), "audio", False)
    gdl_config.set(("extractor", "tiktok"), "videos", False)

    cookies_file = get_settings().ytdlp_cookie_file
    if cookies_file:
        gdl_config.set(("extractor", "tiktok"), "cookies", cookies_file)

    job = gdl_job.DataJob(url, file=None)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            job.run()
    except Exception as exc:
        raise GalleryDLTikTokError(str(exc)) from exc

    posts: list[dict[str, Any]] = []
    for _msg_type, *rest in job.data:
        for item in rest:
            if isinstance(item, dict) and _is_post_metadata(item):
                posts.append(_build_result(item, url))
                if max_count is not None and len(posts) >= max_count:
                    return posts

    return posts


def extract_tiktok_post(url: str) -> dict[str, Any] | None:
    posts = extract_tiktok_posts(url, max_count=1)
    return posts[0] if posts else None
