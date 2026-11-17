"""Shared job identity. Similar titles are hints, never proof of a duplicate."""
import re
from urllib.parse import urlsplit, parse_qsl, urlencode


def job_key(url):
    p = urlsplit((url or '').strip())
    host = (p.hostname or '').lower()
    path = p.path.rstrip('/')
    query = dict(parse_qsl(p.query))
    if host == 'joinhandshake.com' or host.endswith('.joinhandshake.com'):
        match = re.search(r'/(?:job-search|(?:stu/)?jobs)/(\d+)', path)
        if match:
            return 'handshake:' + match[1]
    if query.get('gh_jid', '').isdigit():
        return 'greenhouse:' + query['gh_jid']
    if host in ('boards.greenhouse.io', 'job-boards.greenhouse.io'):
        match = re.search(r'/jobs/(\d+)', path)
        if match:
            return 'greenhouse:' + match[1]
    if host == 'linkedin.com' or host.endswith('.linkedin.com'):
        match = re.search(r'/jobs/view/(?:[^/]*-)?(\d+)', path)
        ident = match[1] if match else query.get('currentJobId')
        if ident:
            return 'linkedin:' + ident
    if host == 'jobs.ashbyhq.com':
        parts = path.strip('/').split('/')
        if len(parts) >= 2:
            return 'ashby:' + '/'.join(parts[:2])
    clean = sorted((k, v) for k, v in parse_qsl(p.query)
                   if not k.lower().startswith('utm_') and k.lower() not in ('gh_src', 'source', 'trackingid'))
    return host + path + ('?' + urlencode(clean) if clean else '')


def employer_key(record):
    url = record.get('sourceUrl') or record.get('normalizedUrl', '')
    p = urlsplit(url)
    if p.hostname in ('boards.greenhouse.io', 'job-boards.greenhouse.io', 'jobs.ashbyhq.com'):
        return p.path.strip('/').split('/')[0].lower()
    title = record.get('title', '')
    return (record.get('company') or (title.split('|', 1)[1].strip() if '|' in title else '')).lower()


def prepare_records(records, excluded=()):
    blocked = {job_key(url) for url in excluded}
    seen, result = set(), []
    for original in records:
        key = job_key(original.get('sourceUrl') or original.get('normalizedUrl', ''))
        if key and (key in blocked or key in seen):
            continue
        if key:
            seen.add(key)
        result.append(dict(original))
    groups = {}
    for record in result:
        if record.get('status') == 'needs_human':
            continue
        company = employer_key(record)
        title = re.sub(r'[^\w]+', ' ', record.get('title', '').split('|')[0].lower()).strip()
        family = company + ':' + title if company and title else ''
        record['job_key'] = job_key(record.get('sourceUrl') or record.get('normalizedUrl', ''))
        record['job_family'] = family
        if family:
            groups.setdefault(family, []).append(record.get('sourceUrl', ''))
    for record in result:
        if record.get('status') == 'needs_human':
            continue
        record['related_job_urls'] = [u for u in groups.get(record['job_family'], []) if u != record.get('sourceUrl')]
    return result


# ── Repost guard (2026-10-26) ────────────────────────────────────────────────
# Employers often close and re-open the same role under a new platform ID, so
# ID-based dedup misses it. A normalized company + title match against a recent
# Applied row is treated as a *likely* duplicate: held back by default, never
# silently discarded, and the user can still choose to apply.
import csv as _csv
from datetime import date as _date, timedelta as _timedelta

_COMPANY_NOISE = re.compile(r'\b(?:inc|llc|ltd|l\.?l\.?c|corp|corporation|co|company|plc|gmbh)\b\.?')
_TITLE_NOISE = re.compile(r'\b(?:remote|hybrid|on ?site|wfh|work from home|20\d\d)\b')


def norm_company(name):
    text = _COMPANY_NOISE.sub(' ', (name or '').lower().replace('&', ' and '))
    return re.sub(r'[^a-z0-9]+', '', text)


def norm_title(title, company=''):
    text = (title or '').split('|')[0].lower().replace('&', ' and ')
    text = re.sub(r'\((?:[^)]*\b(?:remote|hybrid|on ?site)\b[^)]*)\)', ' ', text)
    if company:
        text = re.sub(r'\s[-–]\s*' + re.escape(company.lower()) + r'.*$', ' ', text)
    text = _TITLE_NOISE.sub(' ', text)
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def repost_family(company, title):
    c, t = norm_company(company), norm_title(title, company)
    return c + ':' + t if c and t else ''


def recent_applied_families(csv_path, days=60, today=None):
    """Map repost family -> (date, position, job_key) for Applied rows within `days`."""
    today = today or _date.today()
    result = {}
    try:
        with open(csv_path, encoding='utf-8-sig', newline='') as f:
            rows = list(_csv.DictReader(f))
    except FileNotFoundError:
        return result
    for row in rows:
        if not row.get('Status', '').strip().lower().startswith('applied'):
            continue
        try:
            applied = _date.fromisoformat(row.get('Date Applied', '').strip()[:10])
        except ValueError:
            continue
        if (today - applied).days > days:
            continue
        family = repost_family(row.get('Company', ''), row.get('Position', ''))
        if family and (family not in result or result[family][0] < applied.isoformat()):
            result[family] = (applied.isoformat(), row.get('Position', ''), job_key(row.get('Job URL', '')))
    return result


# ── Official (employer ATS) job identity (2026-10-26) ───────────────────────
# The same employer posting can appear under several LinkedIn/Handshake IDs.
# The ATS posting ID (e.g. Greenhouse 8017340003) is the reliable identity.
ATS_HOSTS = ('boards.greenhouse.io', 'job-boards.greenhouse.io', 'jobs.lever.co', 'jobs.eu.lever.co',
             'jobs.ashbyhq.com', 'apply.workable.com')
OFFICIAL_FIELDS = ('official', 'official_url', 'application_url', 'Official URL')


ATS_SUFFIXES = ('greenhouse.io', 'lever.co', 'ashbyhq.com', 'workable.com', 'myworkdayjobs.com', 'icims.com',
                'smartrecruiters.com', 'recruitee.com', 'applytojob.com', 'applicantpro.com', 'jobs.gem.com',
                'paylocity.com', 'jobvite.com', 'bamboohr.com', 'jibeapply.com', 'rippling.com', 'dover.com')


def official_key(url):
    """Stable key of an employer ATS posting URL, or '' when the URL is not a recognized ATS posting.

    The posting page and its apply/application page give the same key."""
    p = urlsplit((url or '').strip())
    host = (p.hostname or '').lower()
    if p.scheme not in ('http', 'https'):
        return ''
    if dict(parse_qsl(p.query)).get('gh_jid', '').isdigit():
        return job_key(url)
    if not (host in ATS_HOSTS or any(host == s or host.endswith('.' + s) for s in ATS_SUFFIXES)):
        return ''
    key = job_key(url)
    if key.startswith(('greenhouse:', 'ashby:')):
        return key
    path = re.sub(r'/(?:apply|application|applications|login|thanks|confirmation)(?:/.*)?$', '', p.path.rstrip('/'), flags=re.I)
    return host + path if len(path.strip('/').split('/')) >= 2 else ''


def record_official_key(record):
    for field in OFFICIAL_FIELDS + ('sourceUrl', 'url', 'Job URL'):
        key = official_key(record.get(field, ''))
        if key:
            return key
    return ''


def applied_official_keys(csv_path):
    """Map official key -> (date, position) for every Applied row with a known official posting."""
    result = {}
    try:
        with open(csv_path, encoding='utf-8-sig', newline='') as f:
            rows = list(_csv.DictReader(f))
    except FileNotFoundError:
        return result
    for row in rows:
        if row.get('Status', '').strip().lower().startswith('applied'):
            key = record_official_key(row)
            if key:
                result[key] = (row.get('Date Applied', '')[:10], row.get('Position', ''))
    return result


def analyzed_index(root, exclude_dir=None, days=60, today=None):
    """Roles already analysed in earlier batches (any outcome), so a repost is not sent to the LLM again.

    Returns (families, officials): repost family / official ATS key -> (date, title).
    Records whose analysis or materials failed are left out so they can be retried."""
    import json as _json
    from pathlib import Path as _Path
    root = _Path(root)
    exclude = _Path(exclude_dir).resolve() if exclude_dir else None
    today = today or _date.today()
    families, officials = {}, {}
    for queue in sorted((root / 'tmp').glob('**/queue.json')):
        if exclude and queue.parent.resolve() == exclude:
            continue
        m = re.search(r'(20\d\d)-?(\d\d)-?(\d\d)', str(queue.parent))
        try:
            rows = _json.loads(queue.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            continue
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict) or not r.get('analysis') or r.get('status') in ('analysis_failed', 'materials_failed'):
                continue
            when = str(r.get('fetched_at') or (f'{m[1]}-{m[2]}-{m[3]}' if m else ''))[:10]
            try:
                if (today - _date.fromisoformat(when)).days > days:
                    continue
            except ValueError:
                pass
            info = (when, r.get('title') or r.get('role', ''))
            family = repost_family(r.get('company', ''), r.get('title') or r.get('role', ''))
            if family:
                families.setdefault(family, info)
            official = record_official_key(r)
            if official:
                officials.setdefault(official, info)
    return families, officials
