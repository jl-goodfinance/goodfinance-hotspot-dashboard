#!/usr/bin/env python3
"""Use Apify to refresh the two public Facebook pages in social.json.

Designed for GitHub Actions. If APIFY_TOKEN is absent, the script exits cleanly
and the dashboard keeps the last successful social.json snapshot.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parent.parent
SOCIAL = ROOT / "social.json"
ACTOR_ID = os.getenv(
    "APIFY_FB_ACTOR_ID", "lanky_quantifier~facebook-public-scraper"
)
PAGES = {
    "gooaye": "https://www.facebook.com/Gooaye",
    "banini": "https://www.facebook.com/DieWithoutBang/",
}


def request_items(token: str) -> list[dict]:
    actor = urllib.parse.quote(ACTOR_ID, safe="~")
    url = (
        f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
        f"?token={urllib.parse.quote(token)}&timeout=180"
    )
    payload = {
        "pages": list(PAGES.values()),
        "maxPosts": 5,
        "includeComments": False,
        "proxyConfiguration": {"useApifyProxy": True},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "GoodFinance/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=210) as response:
        return json.loads(response.read().decode("utf-8"))


def page_key(item: dict) -> str | None:
    haystack = " ".join(
        str(item.get(k, ""))
        for k in ("pageUrl", "facebookUrl", "postUrl", "url", "pageName")
    ).lower()
    if "gooaye" in haystack or "股癌" in haystack:
        return "gooaye"
    if "diewithoutbang" in haystack or "巴逆逆" in haystack:
        return "banini"
    return None


def normalize_time(item: dict) -> str:
    raw = item.get("publishedAt") or item.get("timestamp") or item.get("date")
    try:
        if isinstance(raw, (int, float)):
            dt = datetime.fromtimestamp(raw, TZ)
        else:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(TZ)
        return dt.strftime("%m/%d %H:%M")
    except Exception:
        return "最新"


def normalize(items: list[dict]) -> dict[str, list[dict]]:
    grouped = {key: [] for key in PAGES}
    seen = set()
    for item in items:
        key = page_key(item)
        text = str(
            item.get("postText")
            or item.get("message")
            or item.get("text")
            or item.get("content")
            or ""
        ).strip()
        link = item.get("postUrl") or item.get("url") or item.get("facebookUrl")
        fingerprint = (key, link or text[:100])
        if not key or not text or fingerprint in seen:
            continue
        if "置頂" in text or "斂財連結" in text:
            continue
        seen.add(fingerprint)
        grouped[key].append(
            {"t": normalize_time(item), "text": " ".join(text.split())[:220], "link": link}
        )
    for key in grouped:
        grouped[key] = grouped[key][:5]
    return grouped


def main() -> int:
    token = os.getenv("APIFY_TOKEN", "").strip()
    if not token:
        print("APIFY_TOKEN not configured; keeping existing FB posts")
        return 0
    try:
        grouped = normalize(request_items(token))
    except Exception as exc:
        print(f"Apify FB refresh failed; keeping existing data: {exc}", file=sys.stderr)
        return 0
    if not any(grouped.values()):
        print("Apify returned no usable posts; keeping existing data")
        return 0

    data = json.loads(SOCIAL.read_text(encoding="utf-8"))
    updated_keys = []
    for key, posts in grouped.items():
        if posts:
            data[key] = posts
            updated_keys.append(f"{key}:{len(posts)}")
    data["updated"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    data["source"] = "Apify · Facebook 公開粉專"
    data["insight_stale"] = True
    SOCIAL.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Apify FB updated " + ", ".join(updated_keys))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
