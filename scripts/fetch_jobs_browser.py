"""
岗位发现 — LinkedIn 搜索 + Handshake，不使用历史固定公司列表。

双轨策略：
  轨道 A: LinkedIn Easy Apply (f_AL=true)
          → 留 LinkedIn job URL → batch_apply.py 直接 Easy Apply
  轨道 B: LinkedIn 全量搜索 (无 f_AL 过滤)
          → 检测 Apply 按钮跳转目标 URL
          → 识别 Greenhouse / Ashby / Workday → 用真实 ATS URL 入队
          → 其他外链 → handoff
  轨道 C: Handshake 直搜 → Quick Apply

公开访客页并非稳定访问保证。登录墙与安全验证必须记录为阻塞，不绕过。
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from runtime_config import launch_discovery_browser_context
from job_identity import job_key, prepare_records
from search_rotation import load_queries, ordered_queries, record_attempt, STATE, linkedin_search_url
from build_apply_queue import load_submitted_urls, load_excluded_urls

# Persistent cross-run URL deduplication
try:
    import url_cache as _url_cache
except ImportError:
    _url_cache = None  # graceful degradation

try:
    from playwright.sync_api import (
        Error as PWError,
        TimeoutError as PWTimeout,
        sync_playwright,
    )
except ImportError:
    sys.exit("需要先安装: pip install playwright && playwright install chromium")

# ── 搜索配置 ──────────────────────────────────────────────────────────────────
# 轨道 A: Easy Apply only
LINKEDIN_EASY_APPLY_TPL = (
    "https://www.linkedin.com/jobs/search/"
    "?keywords={query}&f_AL=true&f_E=2&f_JT=F&sortBy=DD"
)
# 轨道 B: 全量（去掉 f_AL），找 Greenhouse/Ashby/Workday 岗位
LINKEDIN_ALL_TPL = (
    "https://www.linkedin.com/jobs/search/"
    "?keywords={query}&f_E=2&f_JT=F&sortBy=DD"
)

# 两个轨道共用同一组关键词
LINKEDIN_QUERIES = load_queries()

# Handshake
HANDSHAKE_SEARCH_URL = "https://smu.joinhandshake.com/job-search"

# 已知 ATS 域名 → platform 名
ATS_DOMAIN_MAP = {
    "greenhouse.io":       "Greenhouse",
    "grnh.se":             "Greenhouse",
    "boards.greenhouse.io":"Greenhouse",
    "ashbyhq.com":         "Ashby",
    "jobs.ashbyhq.com":    "Ashby",
    "myworkdayjobs.com":   "Workday",
    "workday.com":         "Workday",
    "lever.co":            "Lever",      # 暂时 handoff，以后可加 apply_lever
    "icims.com":           "iCIMS",      # handoff
    "taleo.net":           "Taleo",      # handoff
}
SUPPORTED_PLATFORMS = {"Greenhouse", "Ashby", "Workday"}

# 过滤词：跳过非应届岗位
SKIP_TITLE_WORDS = {
    "senior", "sr.", "sr ", "staff", "principal", "lead ", "manager",
    "director", "vp ", "head of", "10+ year", "8+ year", "7+ year",
    "6+ year", "5+ year", "intern", "internship", "co-op", "coop",
}
TARGET_TITLE_WORDS = {
    "software", "backend", "developer", "machine learning", "full stack",
    "fullstack", "data engineer", "ai engineer", "platform engineer",
}

OUTPUT_PATH = "tmp/public-jds.json"
APPLIED_CSV = "applications/applications.csv"


# ── Helpers ───────────────────────────────────────────────────────────────────
def load_applied_urls() -> set:
    urls = set()
    p = Path(APPLIED_CSV)
    if not p.exists():
        return urls
    with open(p, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            u = (row.get("Job URL") or "").strip()
            if u:
                urls.add(u)
    return urls

def is_skip_title(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in SKIP_TITLE_WORDS)

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]

def detect_ats_from_url(url: str) -> str:
    """从 URL 识别 ATS 平台名，未知返回 'Other'。"""
    for domain, platform in ATS_DOMAIN_MAP.items():
        if domain in url:
            return platform
    return "Other"

def normalize_linkedin_url(href: str) -> str:
    url = re.sub(r'\?.*', '', href)
    if "linkedin.com" not in url:
        url = "https://www.linkedin.com" + url
    return url


def _needs_human_record(url: str, source: str, reason: str) -> dict:
    return {
        "sourceUrl": url,
        "normalizedUrl": url,
        "title": "",
        "text": "",
        "contentHash": "",
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "status": "needs_human",
        "error": reason,
        "_source": source,
    }


def _is_login_page(page) -> bool:
    url = (page.url or "").lower()
    return any(marker in url for marker in ("/login", "/authwall", "/checkpoint"))


def discovery_blocker(body):
    text=body.lower()
    if any(x in text for x in ('正在进行安全验证','verify you are human','checking your browser','security verification')):
        return 'security_verification'
    if 'sign in to handshake' in text or 'log in to handshake' in text:
        return 'login_required'
    return None


def discovery_summary(records, elapsed):
    good=[r for r in records if r.get('status')=='fetched' and r.get('text', 'nonempty')]
    counts={}
    for r in good: counts[r.get('_source','Other')]=counts.get(r.get('_source','Other'),0)+1
    return {'fetched':len(good),'failures':len(records)-len(good),'by_source':counts,
            'elapsed_seconds':round(elapsed,2),'submission_performed':False}


def scrape_linkedin_guest(page, max_items, applied, seen, query):
    """Read the observed public search-card layout; never click Apply or require login."""
    candidates=[]
    for card in page.locator('.base-search-card').all():
        title=card.locator('.base-search-card__title').inner_text().strip()
        if is_skip_title(title): continue
        href=card.locator('a.base-card__full-link').get_attribute('href') or ''
        url=normalize_linkedin_url(href)
        if not href or job_key(url) in applied or job_key(url) in seen: continue
        company=card.locator('.base-search-card__subtitle').inner_text().strip()
        location=card.locator('.job-search-card__location').inner_text().strip().replace('美国','United States')
        dates=card.locator('time')
        candidates.append({'sourceUrl':url,'title':title,'company':company,'location':location,
                           'published_at':dates.first.get_attribute('datetime') if dates.count() else ''})
    records=[]
    detail=page.context.new_page()
    try:
        for item in candidates[:max_items]:
            try:
                detail.goto(item['sourceUrl'],wait_until='domcontentloaded',timeout=20000)
                if _is_login_page(detail):
                    records.append(_needs_human_record(item['sourceUrl'],'LinkedIn','login_required'));continue
                blocker=discovery_blocker(detail.locator('body').inner_text(timeout=5000))
                if blocker:
                    records.append(_needs_human_record(item['sourceUrl'],'LinkedIn',blocker));break
                text=detail.locator('.description__text').inner_text(timeout=5000).strip()
                if not text: raise ValueError('empty_job_description')
                record={**_make_record(item['sourceUrl'],item['title'],text,'LinkedIn'),**item,
                        'discovery_mode':'public_guest','search_keyword':query,
                        'easy_apply_verified':False,'official_requirements_verified':False}
                records.append(record);seen.add(job_key(item['sourceUrl']))
            except (PWError,ValueError) as exc:
                records.append(_needs_human_record(item['sourceUrl'],'LinkedIn',str(exc)))
    finally:
        detail.close()
    return records


def is_easy_apply_card_text(text: str) -> bool:
    """Return true only when a LinkedIn result card explicitly offers Easy Apply."""
    normalized = text.lower()
    return "easy apply" in normalized or "轻松申请" in normalized


def setup_login() -> None:
    from discovery_manager import DiscoveryManager
    from manual_workbench import Workbench
    root=Path(__file__).resolve().parents[1]
    result=DiscoveryManager(Workbench(root,[])).start({'mode':'login'})
    print(result['message'])


# ── 轨道 A: LinkedIn Easy Apply ───────────────────────────────────────────────
def scrape_linkedin_easy_apply(page, query: str, max_items: int, applied: set, seen: set,
                               location='United States', posted_days=14, workplace_types=('1','2','3')) -> list:
    url = linkedin_search_url(query, location, posted_days, workplace_types, True)
    return _scrape_linkedin_common(page, url, max_items, applied, seen,
                                   mode="easy_apply", query=query)

# ── 轨道 B: LinkedIn 全量 → 检测外部 ATS ─────────────────────────────────────
def scrape_linkedin_all(page, query: str, max_items: int, applied: set, seen: set,
                        location='United States', posted_days=14, workplace_types=('1','2','3')) -> list:
    url = linkedin_search_url(query, location, posted_days, workplace_types)
    return _scrape_linkedin_common(page, url, max_items, applied, seen,
                                   mode="all_ats", query=query)


def _scrape_linkedin_common(page, search_url: str, max_items: int,
                             applied: set, seen: set, mode: str, query: str) -> list:
    records = []
    try:
        page.goto(search_url, timeout=30_000)
        page.wait_for_load_state("domcontentloaded", timeout=20_000)
    except (PWTimeout, PWError) as exc:
        print(f"  [{mode}] LinkedIn 搜索失败: {query}: {exc}")
        return [_needs_human_record(search_url, "LinkedIn", str(exc))]
    if _is_login_page(page):
        return [_needs_human_record(search_url, "LinkedIn", "LinkedIn login required")]
    blocker=discovery_blocker(page.locator('body').inner_text(timeout=5000))
    if blocker: return [_needs_human_record(search_url,'LinkedIn',blocker)]
    if page.locator('.base-search-card').count():
        return scrape_linkedin_guest(page,max_items,applied,seen,query)

    for page_num in range(3):
        if len(records) >= max_items:
            break
        try:
            page.wait_for_selector(
                ".base-search-card, .jobs-search-results__list-item, li.occludable-update", timeout=10_000)
        except PWTimeout:
            if not records:
                reason='login_required' if _is_login_page(page) else 'job_cards_not_found; not verified as zero results'
                records.append({**_needs_human_record(search_url,'LinkedIn',reason),
                                'page_url':page.url,'page_title':page.title(),
                                'visible_excerpt':page.locator('body').inner_text(timeout=5000)[:1500]})
            break

        if page.locator('.base-search-card').count():
            return scrape_linkedin_guest(page,max_items,applied,seen,query)

        cards = page.locator(
            ".jobs-search-results__list-item, li.occludable-update").all()
        print(f"  [{mode}|{query[:20]}] 第{page_num+1}页: {len(cards)} 张")

        for card in cards:
            if len(records) >= max_items:
                break
            try:
                title_el = card.locator(
                    ".job-card-list__title, a.job-card-container__link").first
                title = (title_el.text_content(timeout=2000) or "").strip()
                if not title or is_skip_title(title):
                    continue

                company_el = card.locator(
                    ".job-card-container__primary-description, "
                    ".artdeco-entity-lockup__subtitle").first
                company = (company_el.text_content(timeout=2000) or "Unknown").strip()

                link_el = card.locator("a[href*='/jobs/view/']").first
                href = link_el.get_attribute("href", timeout=2000) or ""
                if not href:
                    continue
                li_url = normalize_linkedin_url(href)

                # 轨道 A：Easy Apply 直接用 LinkedIn URL
                if mode == "easy_apply":
                    if job_key(li_url) in applied or job_key(li_url) in seen:
                        continue
                    if not is_easy_apply_card_text(card.inner_text(timeout=2000)):
                        continue
                    seen.add(job_key(li_url))
                    card.click(timeout=3000)
                    page.wait_for_timeout(1200)
                    desc_el = page.locator(
                        ".jobs-description__content, "
                        ".job-view-layout .jobs-box__html-content").first
                    jd_text = (desc_el.inner_text(timeout=3000)
                               if desc_el.is_visible(timeout=3000) else "")
                    if not jd_text:
                        continue
                    records.append(_make_record(
                        li_url, f"{title} | {company}",
                        jd_text, "LinkedIn"))

                # 轨道 B：找外部 ATS URL
                else:
                    # 先检查这张卡是否是 Easy Apply（已被轨道 A 收录，跳过）
                    easy_btn = card.locator("button:has-text('Easy Apply')").first
                    if easy_btn.is_visible(timeout=1000):
                        continue

                    # 点进详情，找 Apply 按钮的跳转 URL
                    card.click(timeout=3000)
                    page.wait_for_timeout(1200)

                    # 尝试从 Apply 按钮拿外部 URL（不点击，避免弹窗）
                    ext_url = ""
                    apply_link = page.locator(
                        "a[data-tracking-control-name*='apply'], "
                        "a.jobs-apply-button, "
                        ".jobs-apply-button--top-card a"
                    ).first
                    if apply_link.is_visible(timeout=2000):
                        raw = apply_link.get_attribute("href") or ""
                        # LinkedIn 有时用 /jobs/view/.../apply 重定向，
                        # 解析 externalApplyLink 参数
                        m = re.search(r'externalApplyLink=([^&]+)', raw)
                        if m:
                            from urllib.parse import unquote
                            ext_url = unquote(m.group(1))
                        elif raw.startswith("http") and "linkedin.com" not in raw:
                            ext_url = raw

                    if not ext_url:
                        # 备选：从页面文本里正则提取
                        html = page.content()
                        m = re.search(
                            r'"applyUrl"\s*:\s*"(https?://[^"]+)"', html)
                        if m:
                            ext_url = m.group(1).replace("\\u0026", "&")

                    if not ext_url:
                        ext_url=li_url

                    platform = detect_ats_from_url(ext_url)

                    if job_key(ext_url) in applied or job_key(ext_url) in seen:
                        continue
                    seen.add(job_key(ext_url))

                    # 拿 JD 文本（从 LinkedIn 详情页）
                    desc_el = page.locator(
                        ".jobs-description__content, "
                        ".job-view-layout .jobs-box__html-content").first
                    jd_text = (desc_el.inner_text(timeout=3000)
                               if desc_el.is_visible(timeout=3000) else "")
                    if not jd_text:
                        continue

                    records.append(_make_record(
                        ext_url, f"{title} | {company}",
                        jd_text, platform))

            except Exception:
                continue

        # 翻页
        next_btn = page.locator("button[aria-label='View next page']").first
        if next_btn.is_visible(timeout=2000):
            next_btn.click()
            page.wait_for_timeout(2000)
        else:
            break

    return records


# ── 轨道 C: Handshake ─────────────────────────────────────────────────────────
def scrape_handshake(page, max_items: int, applied: set, seen: set) -> list:
    records = []
    try:
        page.goto(HANDSHAKE_SEARCH_URL, timeout=30_000)
        page.wait_for_load_state("domcontentloaded", timeout=20_000)
    except (PWTimeout, PWError) as exc:
        print(f"  [Handshake] 搜索失败，跳过: {exc}")
        return [_needs_human_record(HANDSHAKE_SEARCH_URL, "Handshake", str(exc))]
    if _is_login_page(page):
        return [_needs_human_record(HANDSHAKE_SEARCH_URL, "Handshake", "Handshake login required")]
    blocker=discovery_blocker(page.locator('body').inner_text(timeout=5000))
    if blocker: return [_needs_human_record(HANDSHAKE_SEARCH_URL,'Handshake',blocker)]
    try:
        page.wait_for_selector(
            "[data-hook='job-card'], .job-card", timeout=10_000)
    except (PWTimeout, PWError) as exc:
        print(f"  [Handshake] 搜索失败，跳过: {exc}")
        return [_needs_human_record(HANDSHAKE_SEARCH_URL, "Handshake", str(exc))]

    cards = page.locator("[data-hook='job-card'], .job-card, article.card").all()
    print(f"  [Handshake] {len(cards)} 张卡片")

    for card in cards[:max_items]:
        try:
            title_el = card.locator(
                "h3, .job-card__title, [data-hook='job-title']").first
            title = (title_el.text_content(timeout=2000) or "").strip()
            if not title or is_skip_title(title):
                continue

            company_el = card.locator(
                ".employer-name, [data-hook='employer-name']").first
            company = (company_el.text_content(timeout=2000) or "Unknown").strip()

            link_el = card.locator("a[href*='/jobs/']").first
            href = link_el.get_attribute("href", timeout=2000) or ""
            job_url = href if href.startswith("http") \
                else f"https://smu.joinhandshake.com{href}"

            if job_key(job_url) in applied or job_key(job_url) in seen:
                continue
            seen.add(job_key(job_url))

            card.click(timeout=3000)
            page.wait_for_timeout(1500)
            desc_el = page.locator(
                ".job-description, [data-hook='job-description']").first
            jd_text = (desc_el.inner_text(timeout=3000)
                       if desc_el.is_visible(timeout=4000) else "")
            if not jd_text:
                page.go_back(timeout=8000)
                continue

            records.append(_make_record(job_url, f"{title} | {company}",
                                        jd_text, "Handshake"))
            page.go_back(timeout=8000)
            page.wait_for_timeout(800)

        except Exception:
            try:
                page.go_back(timeout=5000)
            except Exception:
                pass

    return records


def write_records_output(output_path: str | Path, records: list[dict]) -> list[dict]:
    """Persist the current deduplicated fetch state so a later site timeout loses no work."""
    unique = dedup(records)
    path = Path(output_path)
    path.parent.mkdir(exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)
    return unique


def _make_record(url: str, title: str, text: str, source: str) -> dict:
    return {
        "sourceUrl":    url,
        "normalizedUrl": url,
        "title":        title,
        "text":         text,
        "contentHash":  sha256(text),
        "fetchedAt":    datetime.now(timezone.utc).isoformat(),
        "status":       "fetched",
        "error":        None,
        "_source":      source,
    }


def dedup(records: list) -> list:
    return prepare_records(records)


def limit_unique_records(records: list[dict], max_items: int) -> list[dict]:
    """Deduplicate a source batch before applying its fetch limit."""
    return dedup(records)[:max_items]


def remaining_total_capacity(records: list[dict], max_total: int) -> int:
    """Return how many successfully fetched jobs may still be collected."""
    fetched = sum(record.get("status") == "fetched" for record in records)
    return max(0, max_total - fetched)


def should_launch_discovery_browser(records: list[dict], max_total: int) -> bool:
    """Avoid starting Playwright when public sources filled the whole batch."""
    return remaining_total_capacity(records, max_total) > 0


# ── 主流程 ────────────────────────────────────────────────────────────────────
def main():
    started=time.monotonic()
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-easy-apply", type=int, default=0,
                        help="本轮 Easy Apply 最多抓多少条")
    parser.add_argument("--max-all-ats",    type=int, default=15,
                        help="本轮全量 LinkedIn 搜索最多抓多少外部 ATS 条目")
    parser.add_argument("--max-handshake", type=int, default=20,
                        help="本轮 Handshake 最多抓多少条")
    parser.add_argument("--max-total", type=int, default=30,
                        help="本轮所有来源合计最多保留多少条成功抓取的岗位")
    parser.add_argument('--max-per-query',type=int,default=5,
                        help='Bound detail reads per keyword so one query does not consume the whole batch')
    # Discovery must not take over a visible Edge window.  Keep the old flag
    # accepted for compatibility, but headless is now the safe default.
    parser.add_argument("--max-queries",type=int,default=3,help="Maximum keyword searches per track")
    parser.add_argument("--visible", action="store_true",
                        help="Show the isolated discovery browser window")
    parser.add_argument("--headless", action="store_false", dest="visible",
                        help=argparse.SUPPRESS)
    parser.add_argument("--setup-login", action="store_true",
                        help="Open the private profile for one-time manual login")
    parser.add_argument("--skip-linkedin",  action="store_true")
    parser.add_argument("--skip-handshake", action="store_true")
    parser.add_argument("--location", help="Override the configured LinkedIn location")
    parser.add_argument("--keyword", action="append", help="Use these keywords instead of the configured list; repeatable")
    parser.add_argument("--posted-days", type=int, help="Only jobs posted within this many days")
    parser.add_argument("--output", default=OUTPUT_PATH,
                        help="Path for fetched job records")
    args = parser.parse_args()
    output_path = args.output
    search_config = json.loads((Path(__file__).resolve().parents[1] / 'config/batch-search.json').read_text(encoding='utf-8'))
    queries = args.keyword or LINKEDIN_QUERIES
    location = args.location or search_config['linkedin_locations'][0]
    posted_days = args.posted_days or search_config['linkedin_posted_days']
    workplace_types = search_config['linkedin_workplace_types']

    # --- persistent cross-run deduplication via url_cache ---
    if _url_cache:
        _cache = _url_cache.load()
        seeded = _url_cache.seed_from_csv(_cache)
        if seeded:
            print(f"[cache] seeded {seeded} URLs from applications.csv")
        applied = set(_cache.keys())
    else:
        _cache = None
        applied = load_applied_urls()
    applied = {job_key(u) for u in applied | load_submitted_urls() | load_excluded_urls()}
    seen = set()
    print(f"已申请/已见: {len(applied)} 条 URL\n")

    all_records = []
    write_records_output(output_path, all_records)

    with sync_playwright() as pw:
        if args.setup_login:
            setup_login()
            return
        if (not args.skip_linkedin or not args.skip_handshake) and should_launch_discovery_browser(all_records, args.max_total):
            context = launch_discovery_browser_context(pw, headless=not args.visible)
            page = context.new_page()
        else:
            context = None
            page = None
            print("Platform discovery disabled or no capacity remaining.")

        if context is not None and not args.skip_linkedin:
            print("=== 轨道 A: LinkedIn Easy Apply ===")
            easy_apply_fetched = 0
            for q in ordered_queries(queries, STATE, "easy")[:max(1,min(args.max_queries,15))]:
                remaining = min(
                    args.max_easy_apply - easy_apply_fetched,
                    remaining_total_capacity(all_records, args.max_total),
                    args.max_per_query,
                )
                if remaining <= 0:
                    break
                recs = scrape_linkedin_easy_apply(
                    page, q, remaining, applied, seen, location, posted_days, workplace_types)
                if not args.keyword:
                    record_attempt(queries, STATE, "easy", q)
                all_records.extend(recs)
                write_records_output(output_path, all_records)
                print(f"  {q[:30]}: +{len(recs)}")
                easy_apply_fetched += sum(
                    r.get("status") == "fetched" for r in recs
                )
                if any(r.get("status") == "needs_human" for r in recs):
                    break
                time.sleep(2)

            if args.max_all_ats > 0:
                print("\n=== 轨道 B: LinkedIn 全量 → Greenhouse/Ashby/Workday ===")
                all_ats_fetched = 0
                for q in ordered_queries(queries, STATE, "all")[:max(1,min(args.max_queries,15))]:
                    remaining = min(
                        args.max_all_ats - all_ats_fetched,
                        remaining_total_capacity(all_records, args.max_total),
                        args.max_per_query,
                    )
                    if remaining <= 0:
                        break
                    recs = scrape_linkedin_all(page, q, remaining, applied, seen, location, posted_days, workplace_types)
                    if not args.keyword:
                        record_attempt(queries, STATE, "all", q)
                    all_records.extend(recs)
                    write_records_output(output_path, all_records)
                    print(f"  {q[:30]}: +{len(recs)}")
                    all_ats_fetched += sum(
                        r.get("status") == "fetched" for r in recs
                    )
                    if all_ats_fetched >= args.max_all_ats:
                        break
                    if any(r.get("status") == "needs_human" for r in recs):
                        break
                    time.sleep(2)

        if context is not None and not args.skip_handshake:
            print("\n=== 轨道 C: Handshake ===")
            hs = scrape_handshake(
                page,
                min(
                    args.max_handshake,
                    remaining_total_capacity(all_records, args.max_total),
                ),
                applied,
                seen,
            )
            all_records.extend(hs)
            write_records_output(output_path, all_records)
            print(f"  +{len(hs)}")

        if context is not None:
            context.close()

    unique = write_records_output(output_path, all_records)

    # NOTE: do NOT write to url_cache here.
    # URLs are added to cache by batch_apply.py only after a job is actually
    # handled (submitted or sent to handoff). This prevents a fetch→crash from
    # permanently skipping jobs that were never applied to.
    summary=discovery_summary(unique,time.monotonic()-started)
    Path(output_path).with_suffix('.summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))
    if summary['failures']: sys.exit(2)


if __name__ == "__main__":
    main()
