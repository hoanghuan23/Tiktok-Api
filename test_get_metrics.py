"""
test_stability.py
Chạy: pip install curl_cffi beautifulsoup4
      python test_stability.py
"""

import asyncio
import json
import time
from typing import Any

from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup


# ── Config ────────────────────────────────────────────────────────────────────

# MS_TOKEN = (
#     """OX3FTxlxeCZgoh0uWZ12a6cQt3pn5fg1F9fZrORVQU0l5nGbqNxwqIW1klOklFUKliTYxur5ftYyiMDPKyuRp7XwCZJKP7RWEiXAb9jdXzaSwMgK8I2XjrSMad09iCFFChx_m-v9ZnmA3htqUYCK4KQqYg=="""
# )


URLS = [
    "https://www.tiktok.com/@vtv24news/video/7651904174891879681",
    "https://www.tiktok.com/@vtv24news/video/7651903511629729025",
    "https://www.tiktok.com/@vtv24news/video/7651865915889356040",
    "https://www.tiktok.com/@vtv24news/video/7651864488471252231",
    "https://www.tiktok.com/@vtv24news/video/7651841934817774866",
    "https://www.tiktok.com/@vtv24news/video/7651832752081259783",
    "https://www.tiktok.com/@vtv24news/video/7651841800134610194",
    "https://www.tiktok.com/@vtv24news/video/7651819431223512328",
    "https://www.tiktok.com/@vtv24news/video/7651589485150424328",
    "https://www.tiktok.com/@vtv24news/video/7651815478691728658",
    "https://www.tiktok.com/@vtv24news/video/7651789770925935890",
    "https://www.tiktok.com/@vtv24news/video/7651619314264444168",
    "https://www.tiktok.com/@vtv24news/video/7651786955411262728",
    "https://www.tiktok.com/@vtv24news/video/7651619091727306002",
    "https://www.tiktok.com/@vtv24news/video/7651589191624576263",
    "https://www.tiktok.com/@vtv24news/video/7651589018475384071",
    "https://www.tiktok.com/@vtv24news/video/7651609074261331207",
    "https://www.tiktok.com/@vtv24news/video/7651600236099554578",
    "https://www.tiktok.com/@vtv24news/video/7651598729874623751",
    "https://www.tiktok.com/@vtv24news/video/7651593195419110664",
    "https://www.tiktok.com/@hanoinews.theanh28/video/7652221759394974984",
    "https://www.tiktok.com/@hanoinews.theanh28/video/7652198302984817941",
    "https://www.tiktok.com/@hanoinews.theanh28/video/7652197036262952200",
    "https://www.tiktok.com/@hanoinews.theanh28/video/7652197828864789780",
    "https://www.tiktok.com/@hanoinews.theanh28/video/7652191383503080725",
    "https://www.tiktok.com/@hanoinews.theanh28/video/7652190391827189013",
    "https://www.tiktok.com/@hanoinews.theanh28/video/7652181936701558037",
    "https://www.tiktok.com/@hanoinews.theanh28/video/7652015925809499412",
    "https://www.tiktok.com/@hanoinews.theanh28/video/7651930555709164818",
    "https://www.tiktok.com/@hanoinews.theanh28/video/7651977753893358868",
    "https://www.tiktok.com/@hanoinews.theanh28/video/7651967564733549845",
    "https://www.tiktok.com/@hanoinews.theanh28/video/7651964685549440277",
    "https://www.tiktok.com/@hanoinews.theanh28/video/7651940311098248466",
    "https://www.tiktok.com/@hanoinews.theanh28/video/7651922373549067541",
]

NUM_WORKERS = 3   # số worker song song
TIMEOUT     = 15  # giây
IMPERSONATE = "chrome124"  # curl_cffi giả lập TLS fingerprint Chrome 124
MAX_RETRIES = 3
RETRY_DELAY = 8


# ── Cookies ───────────────────────────────────────────────────────────────────

def make_cookies() -> dict:
    return {
       "sessionid": "0cd3ce8886e1852f99ad3468f69fc33b",
       "tt_csrf_token": "jKPl1t1A-97Ol4ODc_lOWJPH8TI9cAH6r3zI",
    #    "ttwid": "1%7C0e8jYibbezhncrj2DjW2Mu5PuVErXjyOpKQKKVtMhps%7C1781858270%7Cc6829b32241c26ffd9bff98c7348f59e002417097a2db039e9e39d56bdffdc4d",
    #    "msToken": "gus1ow3MrfG4cux3nF8Qwn29Q_vbZvkmWVJ24Bn0nWtj9y918_spmnLA9VnTH6O1rKEQQKeowHEHDjZfv5tYzfR-OGvJfszA0Ady6TzXflKyohY8c1VNZKl4QjDa0IuOFqezy-bE5lmgroOSIvpYRPUX"
    }


# ── Parse ─────────────────────────────────────────────────────────────────────

def extract_metrics(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    tag  = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")

    if tag is None or not tag.string:
        is_waf     = "wafchallengeid" in html or "Please wait" in html
        is_captcha = "captcha" in html.lower()
        raise ValueError(
            f"Không có rehydration script. "
            f"waf={is_waf}, captcha={is_captcha}, html_len={len(html)}"
        )

    data = json.loads(tag.string)
    item = (
        data["__DEFAULT_SCOPE__"]["webapp.video-detail"]
            ["itemInfo"]["itemStruct"]
    )
    stats = item.get("statsV2") or item.get("stats") or {}

    return {
        "video_id": item.get("id"),
        "author": item.get("author", {}).get("uniqueId"),
        "views_count":   int(stats.get("playCount")    or 0),
        "likes_count":   int(stats.get("diggCount")    or 0),
        "comments_count":int(stats.get("commentCount") or 0),
        "shares_count":  int(stats.get("shareCount")   or 0),
        "bookmarks_count":int(stats.get("collectCount") or 0),
    }


# ── Worker ────────────────────────────────────────────────────────────────────

async def fetch_one(
    session: AsyncSession,
    url: str,
    worker_id: int,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        resp = await session.get(url, timeout=TIMEOUT, allow_redirects=True)
        elapsed = time.perf_counter() - t0

        if resp.status_code != 200:
            return {
                "url": url, "worker": worker_id,
                "ok": False, "elapsed": elapsed,
                "error": f"HTTP {resp.status_code}",
            }

        metrics = extract_metrics(resp.text)
        return {
            "url": url, "worker": worker_id,
            "ok": True, "elapsed": elapsed,
            "metrics": metrics,
        }

    except Exception as exc:
        elapsed = time.perf_counter() - t0
        return {
            "url": url, "worker": worker_id,
            "ok": False, "elapsed": elapsed,
            "error": str(exc),
        }
    
def should_retry(result: dict[str, Any]) -> bool:
    if result.get("ok"):
        return False

    error = result.get("error", "").lower()

    return (
        "waf=true" in error
        or "captcha=true" in error
        or "timeout" in error
        or "connection" in error
        or result.get("status_code") in {403, 408, 429, 500, 502, 503, 504}
    )


async def worker(
    worker_id: int,
    queue: asyncio.Queue,
    results: list,
) -> None:
    # Mỗi worker có 1 AsyncSession riêng với TLS fingerprint Chrome thật
    async with AsyncSession(
        impersonate=IMPERSONATE,
        cookies=make_cookies(),
    ) as session:
        has_made_request = False
        while True:
            try:
                url = queue.get_nowait()
            except asyncio.QueueEmpty:
                break


            result = await fetch_one(session, url, worker_id)
            has_made_request = True

            for attempt in range(MAX_RETRIES):
                if not should_retry(result):
                    break

                print(
                    f"[W{worker_id}] ↺ retry {attempt + 1}/{MAX_RETRIES}..."
                    f" error={result.get('error')}"
                )

                await asyncio.sleep(RETRY_DELAY)
                result = await fetch_one(session, url, worker_id)
            
            results.append(result)

            if result["ok"]:
                m = result["metrics"]
                info = f"views={m['views_count']:,}"
                print(
                    f"         video_id={m['video_id']} author=@{m['author']}\n"
                    f"         views={m['views_count']:,} likes={m['likes_count']:,} "
                    f"comments={m['comments_count']:,} shares={m['shares_count']:,} "
                    f"bookmarks={m['bookmarks_count']:,}"
                )
            else:
                info = result["error"]
                print(f"[W{worker_id}] ✗ {url.split('/')[-1]} ({result['elapsed']:.2f}s) {info}")

# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    print(f"{'='*60}")
    print(f"  TikTok Stability Test  (curl_cffi / {IMPERSONATE})")
    print(f"  URLs: {len(URLS)} | Workers: {NUM_WORKERS} | Timeout: {TIMEOUT}s")
    print(f"{'='*60}\n")

    queue: asyncio.Queue = asyncio.Queue()
    for url in URLS:
        await queue.put(url)

    results: list  = []
    t_start = time.perf_counter()

    await asyncio.gather(*[
        asyncio.create_task(worker(i + 1, queue, results))
        for i in range(NUM_WORKERS)
    ])

    total_elapsed = time.perf_counter() - t_start

    ok_list   = [r for r in results if r["ok"]]
    fail_list = [r for r in results if not r["ok"]]

    print(f"\n{'='*60}")
    print(f"  KẾT QUẢ")
    print(f"{'='*60}")
    print(f"  Tổng      : {len(results)}")
    print(f"  Thành công: {len(ok_list)}  ({len(ok_list)/len(results)*100:.0f}%)")
    print(f"  Thất bại  : {len(fail_list)}  ({len(fail_list)/len(results)*100:.0f}%)")
    print(f"  Tổng thời gian  : {total_elapsed:.2f}s")

    if ok_list:
        avg = sum(r["elapsed"] for r in ok_list) / len(ok_list)
        print(f"  Avg latency (ok): {avg:.2f}s")

    if fail_list:
        print(f"\n  LỖI CHI TIẾT:")
        for r in fail_list:
            print(f"    - {r['url'].split('/')[-1]} [W{r['worker']}]: {r['error']}")

    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
