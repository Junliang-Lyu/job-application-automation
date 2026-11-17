"""
url_cache.py — persistent cross-run URL deduplication cache.

Cache file: tmp/url-cache.json
Format:     {url: "YYYY-MM-DD"}  (date the URL was first seen)
Expiry:     30 days (entries older than EXPIRE_DAYS are purged on load)
"""

from __future__ import annotations

import csv
import json
import os
from datetime import date, timedelta

CACHE_PATH = "tmp/url-cache.json"
EXPIRE_DAYS = 30


def load(cache_path: str = CACHE_PATH) -> dict:
    """Load cache from disk, purging entries older than EXPIRE_DAYS. Returns dict."""
    cutoff = (date.today() - timedelta(days=EXPIRE_DAYS)).isoformat()
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, encoding="utf-8") as f:
            raw: dict = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return {url: d for url, d in raw.items() if d >= cutoff}


def save(cache: dict, cache_path: str = CACHE_PATH) -> None:
    """Persist cache to disk (creates directory if needed)."""
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def add(cache: dict, urls) -> int:
    """Add one or more URLs to cache with today's date. Returns count of newly added."""
    today = date.today().isoformat()
    if isinstance(urls, str):
        urls = [urls]
    new = 0
    for url in urls:
        if url and url not in cache:
            cache[url] = today
            new += 1
    return new


def is_seen(cache: dict, url: str) -> bool:
    """Return True if the URL is already in the cache (not expired)."""
    return url in cache


def seed_from_csv(cache: dict, csv_path: str = "applications/applications.csv") -> int:
    """Seed cache from existing applications CSV. Returns count of newly seeded URLs."""
    if not os.path.exists(csv_path):
        return 0
    url_columns = {"url", "job_url", "apply_url", "link", "URL", "Job URL", "Apply URL"}
    seeded = 0
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                for col in url_columns:
                    val = row.get(col, "").strip()
                    if val:
                        seeded += add(cache, val)
                        break
    except (OSError, csv.Error):
        pass
    return seeded
