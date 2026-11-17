"""
从 analyze_jds.py 的输出生成 Computer Use 投递队列。

用法:
  python scripts/build_apply_queue.py \
    --analyzed tmp/analyzed-jds.json \
    --output tmp/cu-apply-queue.json \
    --min-score 55

所有平台统一交给 Codex Computer Use 处理；脚本不投递。
"""

import argparse
import csv
import json
import re
from pathlib import Path
from job_identity import job_key, prepare_records

RESUME_DIR = "resume/main"
RESUME_DEFAULT = "Junliang_Lyu_Main_Resume_v5_1.pdf"
EXCLUSIONS_PATH = Path("applications/excluded-jobs.local.json")

AI_KEYWORDS = {"llm", "rag", "ml", "machine learning", "ai", "nlp",
               "deep learning", "pytorch", "embedding", "generative"}

SALARY_RANGE = re.compile(
    r"\$\s*(\d{2,3}(?:,\d{3})?)\s*(?:-|–|to)\s*\$?\s*(\d{2,3}(?:,\d{3})?)",
    re.IGNORECASE,
)

RESUME_VERSIONS = {"swe": "SWE", "data": "Data", "it_analyst": "IT_Analyst", "ai": "AI"}


def pick_resume(analysis: dict) -> str:
    """Resume version chosen by the analysis (resume_track); falls back to SWE, then the old main resume."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    track = str((analysis or {}).get("resume_track", "")).strip().lower()
    for name in (RESUME_VERSIONS.get(track), "SWE"):
        if name and (root / f"resume/versions/Junliang_Lyu_Resume_{name}.pdf").is_file():
            return f"resume/versions/Junliang_Lyu_Resume_{name}.pdf"
    return f"{RESUME_DIR}/{RESUME_DEFAULT}"


def detect_platform(url: str) -> str:
    if "joinhandshake.com" in url:
        return "Handshake"
    if "linkedin.com" in url:
        return "LinkedIn"
    if "greenhouse.io" in url or "grnh.se" in url:
        return "Greenhouse"
    if "ashbyhq.com" in url:
        return "Ashby"
    if "myworkdayjobs.com" in url or "workday.com" in url:
        return "Workday"
    return "Other"


def salary_expectation_from_text(text: str) -> str:
    """Return a published salary midpoint, otherwise the approved fallback."""
    match = SALARY_RANGE.search(text or "")
    if not match:
        return "Negotiable"
    low = int(match.group(1).replace(",", ""))
    high = int(match.group(2).replace(",", ""))
    if high < low:
        return "Negotiable"
    return f"${(low + high) // 2:,}"


def merge_handoff_entries(existing: list[dict], new_entries: list[dict]) -> list[dict]:
    """Keep unresolved handoffs from earlier runs and avoid duplicate URLs."""
    merged = list(existing)
    known_urls = {job_key(entry.get("url")) for entry in existing if entry.get("url")}
    for entry in new_entries:
        url = entry.get("url")
        if url and job_key(url) in known_urls:
            continue
        merged.append(entry)
        if url:
            known_urls.add(job_key(url))
    return merged


def load_submitted_urls(path: Path = Path("applications/applications.csv")) -> set[str]:
    """Return job URLs whose application record is already terminally applied."""
    if not path.exists():
        return set()
    with path.open(encoding="utf-8", newline="") as f:
        return {
            row.get("Job URL", "").strip()
            for row in csv.DictReader(f)
            if row.get("Job URL", "").strip()
            and row.get("Status", "").strip().lower().startswith("applied")
        }


def load_excluded_urls(path: Path = EXCLUSIONS_PATH) -> set[str]:
    """Return URLs the user or reviewer permanently excluded from applications."""
    if not path.exists():
        return set()
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(records, list):
        return set()
    return {
        record.get("url", "").strip()
        for record in records
        if isinstance(record, dict) and record.get("url", "").strip()
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--analyzed", default="tmp/analyzed-jds.json", help="analyzed-jds.json 路径")
    parser.add_argument("--output", default="tmp/cu-apply-queue.json", help="Computer Use 队列输出路径")
    parser.add_argument("--min-score", type=int, default=55, help="最低匹配分数（0-100）")
    args = parser.parse_args()

    with open(args.analyzed, encoding="utf-8") as f:
        records = json.load(f)

    queue = []
    skipped = []
    submitted_urls = {job_key(u) for u in load_submitted_urls()}
    excluded_urls = {job_key(u) for u in load_excluded_urls()}

    for r in prepare_records(records):
        analysis = r.get("analysis", {})
        if "error" in analysis:
            continue

        score = analysis.get("fit_score", 0)
        worth = analysis.get("worth_applying", False)

        if not worth or score < args.min_score:
            skipped.append({
                "url": r.get("sourceUrl", ""),
                "score": score,
                "reason": analysis.get("fit_reason", ""),
            })
            continue

        url = r.get("sourceUrl") or r.get("normalizedUrl", "")
        if job_key(url) in submitted_urls:
            skipped.append({
                "url": url,
                "score": score,
                "reason": "Already recorded as applied in applications.csv.",
            })
            continue
        if job_key(url) in excluded_urls:
            skipped.append({
                "url": url,
                "score": score,
                "reason": "Permanently excluded by prior review.",
            })
            continue
        platform = detect_platform(url)
        # 从 title 里提取公司名（格式一般是 "Role | Company" 或 "Role at Company"）
        raw_title = r.get("title", "Unknown")
        if "|" in raw_title:
            role, company = [x.strip() for x in raw_title.split("|", 1)]
        elif " at " in raw_title:
            role, company = [x.strip() for x in raw_title.split(" at ", 1)]
        else:
            role, company = raw_title, "Unknown"

        entry = {
            "url":               url,
            "job_key": r["job_key"],
            "job_family": r["job_family"],
            "related_job_urls": r["related_job_urls"],
            "requires_related_job_review": bool(r["related_job_urls"]),
            "platform":          platform,
            "company":           company,
            "role":              role,
            "resume":            pick_resume(analysis),
            "cover_letter_text": analysis.get("cover_letter_body", ""),
            "fit_score":         score,
            "objective":         analysis.get("objective", ""),
            "project_order":     analysis.get("project_order", []),
            "salary_expectation": salary_expectation_from_text(r.get("text", "")),
            "status":            "pending_cu",
        }

        queue.append(entry)

    output = Path(args.output)
    if output.exists():
        queue = merge_handoff_entries(json.loads(output.read_text(encoding="utf-8")), queue)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)

    print(f"Computer Use 队列: {len(queue)} 个  →  {args.output}")
    print(f"已跳过 (分数低/不值得): {len(skipped)} 个\n")

    by_platform = {}
    for item in queue:
        p = item.get("platform") or detect_platform(item.get("url", ""))
        by_platform[p] = by_platform.get(p, 0) + 1
    for p, n in sorted(by_platform.items()):
        print(f"  {p}: {n} 个")

    if skipped:
        print(f"\n跳过示例 (score < {args.min_score}):")
        for s in skipped[:5]:
            print(f"  [{s['score']}] {s['url'][:60]}  — {s['reason'][:50]}")

if __name__ == "__main__":
    main()
