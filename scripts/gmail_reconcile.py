"""Read-only Gmail reconciliation for job applications. Standard library only.

What it does (each run, incremental):
  * reads recruiting-related mail since the first application (read-only scope),
  * matches each message to an applied / "submission unknown" job by company name
    in the sender or subject (never by body text alone),
  * classifies with local keyword rules: confirmation / rejected / OA / interview,
  * confirmation  -> "结果待确认" becomes Applied (Gmail-verified),
  * rejected / OA / interview -> CSV columns Outcome, Outcome Date, Evidence,
  * anything ambiguous goes to a short review list instead of being written.

Email content never leaves this computer and is never sent to DeepSeek.
Only message IDs, dates, senders and subjects are kept (ignored by Git).

One-time setup:  python scripts/gmail_reconcile.py --setup --client <downloaded client JSON>
Run manually:     python scripts/gmail_reconcile.py [--dry-run]
The workbench runs it automatically in the background when it starts.
"""
import argparse
import base64
import csv
import hashlib
import html
import io
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from job_identity import job_key, norm_title, repost_family, _COMPANY_NOISE  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'
API = 'https://gmail.googleapis.com/gmail/v1/users/me'
EXTRA_FIELDS = ['Outcome', 'Outcome Date', 'Evidence']
MAX_MESSAGES = 300

ATS_SENDERS = ['greenhouse.io', 'greenhouse-mail.io', 'lever.co', 'ashbyhq.com', 'myworkday.com',
               'workday.com', 'smartrecruiters.com', 'icims.com', 'workablemail.com', 'workable.com',
               'jobvite.com', 'jobs-noreply@linkedin.com', 'joinhandshake.com', 'hackerrank.com',
               'codesignal.com', 'hirevue.com', 'recruitee.com', 'bamboohr.com', 'rippling.com',
               'paylocity.com', 'successfactors.com', 'oraclecloud.com', 'dover.com']
SUBJECT_WORDS = ['application', 'applying', 'applied', 'interview', 'assessment', 'candidacy',
                 '"next steps"', '"coding challenge"', 'position', 'opportunity']
OUTCOME_NAMES = {'rejected': '拒', 'oa': 'OA', 'interview': '面试'}

# Order matters: the first category that matches wins.
RULES = [
    ('oa', re.compile(r'hackerrank|codesignal|codility|coderpad|hirevue|online assessment|coding (?:challenge|assessment|test)|interview challenge|assessment request'
                      r'|technical assessment|take[- ]home|complete (?:the|an|your|a|our) [\w\s-]{0,30}(?:assessment|challenge|test)', re.I)),
    ('interview', re.compile(r'(?:would like|like to|pleased to|happy to|excited to) (?:invite|schedule|set up|move forward)'
                             r'|please (?:select|choose|pick|book|share|provide) (?:a |your |some )?(?:time|availability|slot)'
                             r'|calendly\.com|goodtime\.io|scheduling link|phone screen(?:ing)? (?:with|invitation)'
                             r'|invit(?:e|ation) (?:you )?to (?:an? )?(?:interview|phone|video|virtual|onsite)', re.I)),
    ('rejected', re.compile(r'not (?:to )?(?:be )?mov(?:e|ing) forward|decided to (?:move forward|proceed|pursue|go) with other'
                            r'|(?:other|another) candidates? whose|no longer (?:under consideration|being considered)'
                            r'|will not be (?:moving|proceeding|advancing)|(?:have|has) not been selected|not been selected'
                            r'|position has (?:been|now been) filled|we regret to|unfortunately,? (?:we|after|the|this|your)[^.]{0,80}'
                            r'(?:not|unable to offer|decided|other candidates)', re.I)),
    ('confirmed', re.compile(r'thank(?:s| you) for (?:applying|your application|submitting)|received your application'
                             r'|application (?:was|has been) (?:sent|received|submitted)|we(?:\'ve| have) received'
                             r'|successfully (?:submitted|applied)|application (?:confirmation|received)|your application to', re.I)),
]


class NeedsAuth(Exception):
    pass


class GmailError(Exception):
    """An HTTP error from the Gmail API with Google's own reason text."""
    def __init__(self, code, reason, message):
        super().__init__(f'HTTP {code} {reason}: {message}')
        self.code, self.reason, self.message = code, reason, message


# ── Credentials (kept outside the project folder) ───────────────────────────
def config_dir():
    base = os.environ.get('APPDATA') or str(Path.home() / '.config')
    return Path(base) / 'job-search'


def _post(url, data):
    req = urllib.request.Request(url, urllib.parse.urlencode(data).encode(), method='POST')
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _client():
    client = json.loads((config_dir() / 'gmail-client.json').read_text(encoding='utf-8'))
    return client.get('installed') or client.get('web') or client


def setup(client_file=None):
    folder = config_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'gmail-client.json'
    if client_file:
        target.write_text(Path(client_file).read_text(encoding='utf-8'), encoding='utf-8')
    if not target.exists():
        sys.exit(f'缺少 OAuth 客户端文件。请用 --client 指定从 Google Cloud 下载的 JSON。目标位置：{target}')
    client = _client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    state, result = secrets.token_urlsafe(16), {}

    class Catch(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            if q.get('state') == state:
                result.update(q)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write('<h2>Gmail 只读授权完成，可以关闭这个页面。</h2>'.encode())

    server = HTTPServer(('127.0.0.1', 0), Catch)
    server.timeout = 5
    redirect = f'http://127.0.0.1:{server.server_port}/'
    url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode(dict(
        client_id=client['client_id'], redirect_uri=redirect, response_type='code', scope=SCOPE,
        access_type='offline', prompt='consent', state=state,
        code_challenge=challenge, code_challenge_method='S256'))
    print('请在浏览器里登录求职 Gmail 并同意“只读”权限。若没有自动打开，复制这个链接：\n' + url, flush=True)
    webbrowser.open(url)
    deadline = time.time() + 300
    while 'code' not in result and 'error' not in result and time.time() < deadline:
        server.handle_request()
    server.server_close()
    if 'code' not in result:
        sys.exit('授权未完成：' + result.get('error', '超时'))
    token = _post('https://oauth2.googleapis.com/token', dict(
        code=result['code'], client_id=client['client_id'], client_secret=client.get('client_secret', ''),
        redirect_uri=redirect, grant_type='authorization_code', code_verifier=verifier))
    if 'refresh_token' not in token:
        sys.exit('Google 没有返回 refresh token，请重新运行 --setup。')
    (folder / 'gmail-token.json').write_text(json.dumps({'refresh_token': token['refresh_token']}), encoding='utf-8')
    print('授权完成。凭证保存在项目外：' + str(folder))


def access_token():
    try:
        client = _client()
        refresh = json.loads((config_dir() / 'gmail-token.json').read_text(encoding='utf-8'))['refresh_token']
    except (OSError, KeyError, ValueError):
        raise NeedsAuth('Gmail 还没授权：运行 python scripts/gmail_reconcile.py --setup --client <下载的JSON>')
    try:
        return _post('https://oauth2.googleapis.com/token', dict(
            client_id=client['client_id'], client_secret=client.get('client_secret', ''),
            refresh_token=refresh, grant_type='refresh_token'))['access_token']
    except urllib.error.HTTPError as e:
        if e.code in (400, 401):
            raise NeedsAuth('Gmail 授权已失效：重新运行 python scripts/gmail_reconcile.py --setup')
        raise


# ── Gmail reading ────────────────────────────────────────────────────────────
class Gmail:
    def __init__(self, token):
        self.token = token

    def get(self, path, **params):
        url = f'{API}/{path}' + ('?' + urllib.parse.urlencode(params, doseq=True) if params else '')
        for attempt in range(5):
            req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + self.token})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                try:
                    err = json.loads(e.read() or b'{}').get('error', {})
                except ValueError:
                    err = {}
                reason = ((err.get('errors') or [{}])[0].get('reason') or err.get('status') or '')
                message = err.get('message') or str(e.reason)
                limited = e.code == 429 or (e.code == 403 and 'rate' in reason.lower()) or e.code >= 500
                if limited and attempt < 4:
                    time.sleep(2 ** attempt)  # back off on rate limits / server hiccups
                    continue
                raise GmailError(e.code, reason, message) from None

    def search(self, query, limit=MAX_MESSAGES):
        ids, page = [], None
        while len(ids) < limit:
            params = dict(q=query, maxResults=min(100, limit - len(ids)))
            if page:
                params['pageToken'] = page
            data = self.get('messages', **params)
            ids += [m['id'] for m in data.get('messages', [])]
            page = data.get('nextPageToken')
            if not page:
                break
        return ids

    def message(self, mid):
        m = self.get(f'messages/{mid}', format='full')
        headers = {h['name'].lower(): h['value'] for h in m.get('payload', {}).get('headers', [])}
        return dict(id=mid, date=datetime.fromtimestamp(int(m['internalDate']) / 1000).date().isoformat(),
                    sender=headers.get('from', ''), subject=headers.get('subject', ''), body=body_text(m.get('payload', {})))


def body_text(payload):
    plain, rich = [], []

    def walk(part):
        data = part.get('body', {}).get('data')
        if data:
            text = base64.urlsafe_b64decode(data + '=' * (-len(data) % 4)).decode('utf-8', 'replace')
            if part.get('mimeType') == 'text/plain':
                plain.append(text)
            elif part.get('mimeType') == 'text/html':
                rich.append(text)
        for p in part.get('parts', []) or []:
            walk(p)
    walk(payload)
    text = '\n'.join(plain) or re.sub(r'<[^>]+>', ' ', html.unescape('\n'.join(rich)))
    return re.sub(r'\s+', ' ', text)[:6000]


def build_query(since):
    senders = ' OR '.join(ATS_SENDERS)
    words = ' OR '.join(SUBJECT_WORDS)
    return f'after:{since:%Y/%m/%d} -in:sent -in:drafts (from:({senders}) OR subject:({words}))'


# ── Matching and classification (pure functions, unit-tested) ───────────────
# Words employers usually drop in their own emails ("Plaid", not "Plaid Technologies").
_MATCH_NOISE = re.compile(r'\b(?:technologies|technology|solutions|services|systems|labs|group|holdings|international|software|consulting)\b')


def _clean(text):
    return re.sub(r"['’]", '', (text or '').lower()).replace('&', ' and ')


def company_tokens(name):
    base = re.findall(r'[a-z0-9]+', _COMPANY_NOISE.sub(' ', _clean(name)))
    return re.findall(r'[a-z0-9]+', _MATCH_NOISE.sub(' ', ' '.join(base))) or base


def company_regex(name):
    tokens = company_tokens(name)
    if not tokens:
        return None
    return re.compile(r'(?<![a-z0-9])' + r'[\s\-.]*'.join(map(re.escape, tokens)) + r'(?![a-z0-9])')


def classify(msg):
    if re.search(r'hackerrank|codesignal|hirevue|codility', msg['sender'], re.I):
        return 'oa'
    text = f"{msg['subject']} {msg['body']}"
    for name, pattern in RULES:
        if pattern.search(text):
            return name
    return None


def match(msg, candidates):
    """Return (hits, reason). The company must appear in the sender name/domain or the subject."""
    header = _clean(msg['sender'] + ' ' + msg['subject'])
    domain = re.search(r'@([\w.-]+)', msg['sender'])
    labels = domain[1].lower().split('.') if domain else []
    hits = []
    for c in candidates:
        rx = company_regex(c['company'])
        if not rx:
            continue
        flat = ''.join(company_tokens(c['company']))
        by_domain = len(flat) >= 4 and flat in labels
        if (rx.search(header) or by_domain) and msg['date'] >= (c.get('since') or '0000'):
            hits.append(c)
    if len(hits) <= 1:
        return hits, ''
    flat_text = re.sub(r'[^a-z0-9]+', ' ', _clean(msg['subject'] + ' ' + msg['body']))
    titled = [c for c in hits if norm_title(c['title'], c['company'])
              and f" {norm_title(c['title'], c['company'])} " in f' {flat_text} ']
    if len(titled) == 1:
        return titled, ''
    # Same role applied to more than once (a repost): the email belongs to the latest application before it.
    if len({repost_family(c['company'], c['title']) for c in hits}) == 1:
        return [max(hits, key=lambda c: c.get('since') or '')], ''
    return hits, 'multiple_roles'


# ── CSV helpers ──────────────────────────────────────────────────────────────
def read_csv(path):
    raw = Path(path).read_bytes()
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    fields = list(reader.fieldnames)
    rows = list(reader)
    for f in EXTRA_FIELDS:
        if f not in fields:
            fields.append(f)
    for r in rows:
        for f in fields:
            r[f] = r.get(f) or ''
    return fields, rows, raw.startswith(b'\xef\xbb\xbf')


def write_csv(path, fields, rows, bom=True):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=fields, lineterminator='\r\n')
    w.writeheader()
    w.writerows(rows)
    tmp = Path(str(path) + '.tmp')
    tmp.write_bytes((b'\xef\xbb\xbf' if bom else b'') + out.getvalue().encode('utf-8'))
    tmp.replace(path)


def candidates_from(rows, workbench):
    cands = []
    for r in rows:
        if r['Status'].lower().startswith(('applied', 'submission unknown')):
            since = ''
            if re.match(r'\d{4}-\d\d-\d\d', r['Date Applied']):
                since = (date.fromisoformat(r['Date Applied'][:10]) - timedelta(days=1)).isoformat()
            cands.append(dict(kind='csv', key=job_key(r['Job URL']), company=r['Company'], title=r['Position'], since=since))
    in_csv = {c['key'] for c in cands}
    if workbench is not None:
        for key, r in workbench.records().items():
            if r.get('status') == 'submission_unknown' and key not in in_csv:
                cands.append(dict(kind='unknown', key=key, company=r.get('company', ''),
                                  title=r.get('title') or r.get('role', ''), since=str(r.get('fetched_at', ''))[:10]))
    return cands


# ── Reconcile ────────────────────────────────────────────────────────────────
def apply_updates(rows, updates, summary):
    """Apply (key, category, date, message id, label) updates to CSV rows in place."""
    by_key = {job_key(r['Job URL']): r for r in rows if r['Job URL']}
    for key, category, when, mid, label in sorted(updates, key=lambda u: u[2]):
        r = by_key.get(key)
        if r is None:
            continue
        if r['Status'] != 'Applied':  # any employer email proves the application arrived
            r['Status'] = 'Applied'
            summary['confirmed'].append(label)
        if category == 'confirmed':
            r['Evidence'] = r['Evidence'] or f'gmail:{mid}'
            continue
        if r['Outcome Date'] <= when:
            r.update({'Outcome': OUTCOME_NAMES[category], 'Outcome Date': when, 'Evidence': f'gmail:{mid}'})
        if r['Last Contact'] < when:
            r['Last Contact'] = when
        summary['outcomes'].append(f'{label} → {OUTCOME_NAMES[category]}')


def reconcile(root=ROOT, workbench=None, gmail=None, dry_run=False):
    from manual_workbench import CSV_LOCK
    root = Path(root)
    csv_path = root / 'applications/applications.csv'
    events_path = root / 'applications/email-events.local.jsonl'
    review_path = root / 'applications/email-review.local.json'
    seen = set()
    if events_path.exists():
        seen = {json.loads(x)['id'] for x in events_path.read_text(encoding='utf-8').splitlines() if x.strip()}
    with CSV_LOCK:
        _, rows, _ = read_csv(csv_path)
    dates = [r['Date Applied'][:10] for r in rows if re.match(r'\d{4}-\d\d-\d\d', r['Date Applied'])]
    since = date.fromisoformat(min(dates)) - timedelta(days=1) if dates else date.today() - timedelta(days=60)
    gmail = gmail or Gmail(access_token())
    new_ids = [i for i in gmail.search(build_query(since)) if i not in seen]
    cands = candidates_from(rows, workbench)
    summary = dict(read=len(new_ids), confirmed=[], outcomes=[], review=[])
    updates, new_events = [], []
    failed = []
    for mid in new_ids:
        try:
            msg = gmail.message(mid)
        except GmailError as e:
            if e.code not in (403, 404):
                raise
            failed.append(e)  # not logged as seen, so it is retried next run
            if len(failed) >= 5 and not new_events and not updates:
                raise  # every message fails: report Google's reason instead of skipping silently
            continue
        category = classify(msg)
        event = dict(id=mid, date=msg['date'], sender=msg['sender'][:200], subject=msg['subject'][:300], category=category)
        if category:
            hits, why = match(msg, cands)
            if len(hits) == 1 and not why:
                c = hits[0]
                label = f"{c['company']} · {c['title']}"
                event['match'] = c['key']
                if c['kind'] == 'unknown':
                    # Not in the CSV yet: record it as applied first (Gmail evidence), then apply outcome.
                    summary['confirmed'].append(label)
                    if not dry_run and workbench is not None:
                        workbench.record(c['key'], 'applied', f"Gmail {msg['date']}：{msg['subject'][:120]}", verification='gmail')
                    c['kind'] = 'csv'
                    if category == 'confirmed':
                        new_events.append(event)
                        continue
                updates.append((c['key'], category, msg['date'], mid, label))
            elif hits and category == 'confirmed' and all(c['kind'] == 'csv' for c in hits):
                event['review'] = 'ignored_confirmation_all_already_applied'  # nothing would change
            elif hits:
                event['review'] = why or 'ambiguous'
                summary['review'].append(dict(event, candidates=[f"{c['company']} · {c['title']}" for c in hits]))
        new_events.append(event)
    if dry_run:
        _, preview, _ = read_csv(csv_path)
        apply_updates(preview, updates, summary)
    else:
        with CSV_LOCK:  # re-read so rows the workbench appended meanwhile are kept
            fields, latest, bom = read_csv(csv_path)
            apply_updates(latest, updates, summary)
            write_csv(csv_path, fields, latest, bom)
        with events_path.open('a', encoding='utf-8') as f:
            for e in new_events:
                f.write(json.dumps(e, ensure_ascii=False) + '\n')
        old = json.loads(review_path.read_text(encoding='utf-8')) if review_path.exists() else []
        review_path.write_text(json.dumps((summary['review'] + old)[:50], ensure_ascii=False, indent=2), encoding='utf-8')
    summary['skipped'] = [str(e) for e in failed]
    summary['confirmed'] = list(dict.fromkeys(summary['confirmed']))
    summary['ran_at'] = datetime.now().isoformat(timespec='seconds')
    return summary


def stats(csv_path, scores=None):
    """Reply rates by platform and fit-score band. `scores` maps job_key -> fit score."""
    _, rows, _ = read_csv(csv_path)
    rows = [r for r in rows if r['Status'].lower().startswith('applied')]
    names = {'OA': 'oa', '面试': 'interview', '拒': 'rejected'}

    def add(groups, name, r):
        g = groups.setdefault(name, dict(applied=0, replied=0, oa=0, interview=0, rejected=0))
        g['applied'] += 1
        if r['Outcome'] in names:
            g['replied'] += 1
            g[names[r['Outcome']]] += 1
    by_platform, by_score = {}, {}
    for r in rows:
        p = r['Platform'].lower()
        add(by_platform, 'LinkedIn' if 'linkedin' in p else 'Handshake' if 'handshake' in p else '其他', r)
        s = (scores or {}).get(job_key(r['Job URL']))
        add(by_score, '未评分' if s is None else '85+' if s >= 85 else '70–84' if s >= 70 else '<70', r)
    return dict(applied=len(rows), replied=sum(1 for r in rows if r['Outcome'] in names),
                by_platform=by_platform, by_score=by_score)


# ── Background run for the workbench ─────────────────────────────────────────
STATUS = dict(state='idle', message='')


def start_background(workbench):
    if STATUS.get('state') == 'running':
        return
    STATUS.update(state='running', message='正在读取 Gmail…')  # set before the thread starts: no double runs

    def run():
        try:
            s = reconcile(workbench.root, workbench)
            STATUS.update(state='done', last=s, message=f"Gmail 对账完成：新邮件 {s['read']} 封，核实已投 {len(s['confirmed'])}，"
                          f"新结果 {len(s['outcomes'])}，待你看一眼 {len(s['review'])}"
                          + (f"，读取失败 {len(s['skipped'])} 封（下次重试）" if s['skipped'] else ''))
        except NeedsAuth as e:
            STATUS.update(state='needs_auth', message=str(e))
        except Exception as e:  # never block the workbench
            STATUS.update(state='error', message=f'Gmail 对账失败：{e}')
        finally:
            STATUS['finished'] = time.time()
    threading.Thread(target=run, daemon=True).start()


REFRESH_SECONDS = 3600


def maybe_refresh(workbench):
    """Called on every page refresh: pick up a new authorization, and re-check hourly while the page stays open."""
    state = STATUS.get('state')
    if state == 'needs_auth' and (config_dir() / 'gmail-token.json').exists():
        start_background(workbench)
    elif state in ('done', 'error') and time.time() - STATUS.get('finished', 0) > REFRESH_SECONDS:
        start_background(workbench)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--setup', action='store_true', help='一次性授权（只读）')
    p.add_argument('--client', help='Google Cloud 下载的 OAuth 客户端 JSON')
    p.add_argument('--dry-run', action='store_true', help='只显示会写入什么，不改任何文件')
    a = p.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if a.setup:
        return setup(a.client)
    from manual_workbench import Workbench
    try:
        s = reconcile(ROOT, Workbench(ROOT, [], discover=True), dry_run=a.dry_run)
    except NeedsAuth as e:
        sys.exit(str(e))
    except GmailError as e:
        sys.exit('Gmail 返回错误（请把这一行发给 Claude）：' + str(e))
    print(('【演习，没有写入任何文件】' if a.dry_run else '') + f"新邮件 {s['read']} 封")
    for x in s['confirmed']:
        print('  核实已投：' + x)
    for x in s['outcomes']:
        print('  新结果：' + x)
    if s['skipped']:
        print(f"  有 {len(s['skipped'])} 封邮件读取失败，已跳过、下次再试。第一条原因：{s['skipped'][0]}")
    for x in s['review']:
        print(f"  待你看一眼：{x['date']} {x['subject'][:70]} ← {'；'.join(x['candidates'])}")


if __name__ == '__main__':
    main()
