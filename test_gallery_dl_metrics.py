"""Probe TikTok post metrics using gallery-dl (metadata-only, no download).

Usage:
    python test_gallery_dl_metrics.py
    python test_gallery_dl_metrics.py https://www.tiktok.com/@mancity/photo/7659174558100000023
    python test_gallery_dl_metrics.py URL --cookies /path/to/cookies.txt
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from datetime import datetime, timezone
from typing import Any

import gallery_dl.config as gdl_config
import gallery_dl.job as gdl_job

DEFAULT_URL = "https://www.tiktok.com/@temu/video/7662816375626419469"


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


def _to_iso_datetime(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None

    timestamp = _to_int(value)
    if timestamp is None:
        return str(value)
    if timestamp > 10_000_000_000:
        timestamp = timestamp // 1000
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None).isoformat()


def _author_username(post: dict) -> str | None:
    author = post.get("author") or post.get("user")
    if isinstance(author, str):
        return author.lstrip("@")
    if isinstance(author, dict):
        username = author.get("uniqueId") or author.get("unique_id") or author.get("nickname")
        return str(username).lstrip("@") if username else None
    return None


def _tiktok_url(post: dict, fallback_url: str) -> str:
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


def _image_urls(post: dict) -> list[str]:
    image_post = post.get("imagePost", {})
    images = []
    for img in image_post.get("images", []):
        url = _first_url(img.get("imageURL") or img.get("imageUrl") or img)
        if url:
            images.append(url)
    return images


def _post_metrics(post: dict) -> dict[str, int | None]:
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
    }


def build_result(post: dict, requested_url: str) -> dict:
    video = post.get("video") or {}
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
        "posted_at": _to_iso_datetime(post.get("date") or post.get("createTime") or post.get("create_time")),
        "metrics": _post_metrics(post),
    }


def is_post_metadata(data: dict) -> bool:
    return bool(data.get("id") and (data.get("stats") or data.get("statsV2") or data.get("imagePost")))


def probe(url: str, cookies_file: str | None) -> dict | None:
    # Metadata-only: don't actually download photos/audio/video files.
    gdl_config.set(("extractor", "tiktok"), "photos", False)
    gdl_config.set(("extractor", "tiktok"), "audio", False)
    gdl_config.set(("extractor", "tiktok"), "videos", False)

    if cookies_file:
        gdl_config.set(("extractor", "tiktok"), "cookies", cookies_file)

    job = gdl_job.DataJob(url, file=None)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        job.run()

    for _msg_type, *rest in job.data:
        for item in rest:
            if isinstance(item, dict) and is_post_metadata(item):
                return build_result(item, url)

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe TikTok photo post via gallery-dl.")
    parser.add_argument("url", nargs="?", default=DEFAULT_URL)
    parser.add_argument("--cookies", type=str, default=None, help="Path to cookies.txt (Mozilla format)")
    args = parser.parse_args()

    result = probe(args.url, args.cookies)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
