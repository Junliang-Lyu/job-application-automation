"""Fetch public Greenhouse and Ashby job boards without browser automation."""

from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.request import Request, urlopen


def read_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "JobSearchPublicATS/1.0"})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def plain_text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(html))).strip()


def record(url: str, title: str, text: str, source: str) -> dict:
    return {
        "sourceUrl": url, "normalizedUrl": url, "title": title,
        "text": text, "contentHash": hashlib.sha256(text.encode()).hexdigest(), "_source": source,
        "status": "fetched", "error": None,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
    }


def failed(source: str, board: str, error: Exception) -> dict:
    return {
        "sourceUrl": board, "normalizedUrl": board, "title": "", "text": "",
        "contentHash": "", "_source": source, "status": "needs_human",
        "error": str(error), "fetchedAt": datetime.now(timezone.utc).isoformat(),
    }


def fetch_greenhouse_board(board: str) -> list[dict]:
    try:
        payload = read_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
        return [{**record(job["absolute_url"], job.get("title", ""), plain_text(job.get("content", "")), "Greenhouse"),
                 "board": board, "company": board, "location": job.get("location", {}).get("name", ""),
                 "published_at": job.get("updated_at", "")}
                for job in payload.get("jobs", [])]
    except Exception as exc:
        return [failed("Greenhouse", board, exc)]


def fetch_ashby_board(board: str) -> list[dict]:
    try:
        payload = read_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
        return [{**record(job.get("jobUrl", ""), job.get("title", ""), plain_text(job.get("descriptionHtml", "")), "Ashby"),
                 "board": board, "company": board, "location": job.get("location", ""),
                 "published_at": job.get("publishedAt", ""), "is_remote": job.get("isRemote", False)}
                for job in payload.get("jobs", [])]
    except Exception as exc:
        return [failed("Ashby", board, exc)]


def fetch_configured_boards(config_path: Path | None = None) -> list[dict]:
    config_path = config_path or Path(__file__).resolve().parents[1] / "config" / "public-ats-boards.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    records: list[dict] = []
    for board in config.get("greenhouse", []):
        records.extend(fetch_greenhouse_board(board))
    for board in config.get("ashby", []):
        records.extend(fetch_ashby_board(board))
    for item in records:
        if item.get("company"):
            item["company"] = config.get("company_names", {}).get(item["company"], item["company"])
    return records
