"""Local manual application desk. No crawling, LLM calls, or ATS automation."""
import argparse
import csv
import json
import re
import secrets
import socket
import threading
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from qualification import split_requirements, hard_conflicts, looks_like_agency
from job_identity import job_key, repost_family, recent_applied_families, record_official_key, applied_official_keys, official_key, OFFICIAL_FIELDS
from workbench_materials import answer_cards, project_cards, bundle, bundle_folder, upload_name, upload_folder, UPLOAD_RESUME_NAME, UPLOAD_COVER_NAME

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ['Date Applied','Company','Position','Location','Job URL','Platform',
          'Company Website Applied','Resume Version','Cover Letter','Status',
          'Contact Name','Contact Email','Follow-up Due','Follow-up Sent','Last Contact','Notes','Official URL']
STATES = {'reviewed','pending','skipped','submission_unknown','applied'}
def code_version(root=None):
    """Fingerprint of the workbench code; the launcher restarts a running server whose code is older."""
    base = Path(root or Path(__file__).resolve().parents[1]) / 'scripts'
    files = sorted(list(base.glob('*.py')) + list(base.glob('*.html')))
    return str(int(max((f.stat().st_mtime for f in files), default=0)))


CODE_VERSION = code_version()
STALE_DAYS = 14        # queued jobs older than this are shown as possibly closed
AGENCY_DAYS = 2        # staffing/placement posts close fast; drop them from the ready list after this
REPOST_WINDOW_DAYS = 60
CSV_LOCK = threading.RLock()  # shared with gmail_reconcile, which runs in a background thread


def batch_date(path):
    """Best-known discovery date for a queue file: folder date stamp, else file time."""
    for part in reversed(path.parts):
        m = re.search(r'(20\d\d)-?(\d\d)-?(\d\d)', part)
        if m:
            try: return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
            except ValueError: pass
    return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()


OLD_RESUME = 'Junliang_Lyu_Main_Resume_v5_1.pdf'
_TITLE_TRACKS = [('ai', r'\b(?:ai|ml|machine learning|llm|genai|generative)\b'),
                 ('it_analyst', r'systems? analyst|\bit\b|business analyst|technical analyst|support|implementation|consultant'),
                 ('data', r'\bdata\b|analytics|\bbi\b|business intelligence|analyst')]


def resume_value(r):
    """Resume for a row: its own choice, else the analysis track, else a guess from the title (never the old main resume)."""
    from build_apply_queue import pick_resume
    value = r.get('resume') or ''
    if value and Path(value).name != OLD_RESUME:
        return value
    analysis = dict(r.get('analysis') or {})
    if not analysis.get('resume_track'):
        title = str(r.get('title') or r.get('role') or '').lower()
        analysis['resume_track'] = next((t for t, rx in _TITLE_TRACKS if re.search(rx, title)), 'swe')
    return pick_resume(analysis)


def safe_url(value):
    try:
        p = urlsplit(str(value or ''))
        return str(value) if p.scheme in ('http','https') and p.hostname and not p.username and not p.password else ''
    except ValueError:
        return ''


class Workbench:
    def __init__(self, root, queues, discover=False):
        self.root = Path(root).resolve()
        self.queues = [Path(p).resolve() for p in queues]
        self.journal = self.root / 'applications/manual-workbench.local.jsonl'
        self.csv_path = self.root / 'applications/applications.csv'
        self.discover = discover

    def sync_materials(self):
        result={}
        for key,r in self.records().items():
            resume=self.resume_file(r)
            letter=self.material(r.get('cover_letter'))
            result[key]=bundle(self.root,r,resume,letter)
        return result

    def resume_file(self, r):
        """The row's resume PDF; the old main resume only if the four versions are missing."""
        return self.material(resume_value(r)) or self.material('resume/main/' + OLD_RESUME)

    def material(self, value):
        if not value: return None
        p = Path(value)
        p = (p if p.is_absolute() else self.root / p).resolve()
        try:
            if not p.is_relative_to(self.root) or p.suffix.lower() not in ('.pdf','.docx','.txt') or not p.is_file():
                return None
        except OSError:  # e.g. a path too long for the file system: treat as missing, never crash the desk
            return None
        return p

    def records(self):
        if self.discover: self.queues=sorted((self.root/'tmp').glob('**/queue.json'))
        rows = {}
        # Terminal results win across batches; manual journal is overlaid last.
        for path in self.queues:
            entries = json.loads(path.read_text(encoding='utf-8-sig'))
            events = path.parent / 'events.jsonl'
            by_key = {}
            found = batch_date(path)
            for r in entries:
                key = r.get('job_key') or job_key(r.get('sourceUrl') or r.get('url',''))
                if key: by_key[key] = dict(r, job_key=key, fetched_at=r.get('fetched_at') or found)
            if events.exists():
                for line in events.read_text(encoding='utf-8').splitlines():
                    e = json.loads(line)
                    if e['job_key'] in by_key: by_key[e['job_key']].update(e)
            for key, r in by_key.items():
                if key not in rows or rows[key].get('status') != 'applied': rows[key] = r
        if self.journal.exists():
            for line in self.journal.read_text(encoding='utf-8').splitlines():
                e = json.loads(line)
                if e['job_key'] in rows: rows[e['job_key']].update(e)
        if self.csv_path.exists():
            with self.csv_path.open(encoding='utf-8-sig', newline='') as f:
                applied = {job_key(r['Job URL']) for r in csv.DictReader(f)
                           if r.get('Status','').lower().startswith('applied')}
            for r in rows.values():
                aliases = [r.get(k,'') for k in ('sourceUrl','url','official_url','application_url','confirmation_url')]
                if any(job_key(u) in applied for u in aliases if u): r['status'] = 'applied'
        return rows

    def data(self):
        jobs = []
        families = recent_applied_families(self.csv_path, REPOST_WINDOW_DAYS)
        officials = applied_official_keys(self.csv_path)
        today = date.today()
        for key, r in self.records().items():
            a = r.get('analysis') or {}
            status = r.get('status','unreviewed')
            status = 'pending' if status in ('reviewed','ready_for_review','awaiting_authorization','in_progress') else status
            note = r.get('reason') or r.get('error','')
            # Only explicit hard limits (citizenship, clearance, required certification...) need the candidate;
            # unstated sponsorship, start date, missing skills etc. become notes (also for jobs analysed earlier).
            blockers, hints = split_requirements(a.get('unknown_requirements') or [])
            if status == 'needs_qualification_review' and not blockers:
                status, note = 'pending', ''
            elif status == 'needs_qualification_review':
                note = '需要核对：' + '；'.join(blockers)
            same_post = officials.get(record_official_key(r)) if status not in ('applied','skipped','submission_unknown') else None
            if same_post:
                status, note = 'duplicate_official', f'确定重复：{same_post[0]} 已投过同一个官网职位（{same_post[1]}），只是 LinkedIn/Handshake 链接不同'
            elif status not in ('applied','skipped','submission_unknown') and not r.get('duplicate_override'):
                prior = families.get(repost_family(r.get('company',''), r.get('title') or r.get('role','')))
                if prior and prior[2] != key:
                    status, note = 'likely_duplicate', f'疑似重复：{prior[0]} 已投过同公司同名岗位（{prior[1]}）'
            try: age = (today - date.fromisoformat(str(r.get('fetched_at',''))[:10])).days
            except ValueError: age = None
            hard = hard_conflicts(a) if status in ('pending','needs_qualification_review') else []
            if hard:
                status, note = 'not_recommended', '；'.join(hard)
            agency = bool(r.get('agency') or looks_like_agency(a, r.get('text',''), r.get('company','')))
            if agency:
                hints = [f'疑似中介或人力外包贴：下架快，排在最后，超过 {AGENCY_DAYS} 天不再推荐'] + list(hints)
            resume = self.resume_file(r)
            letter = self.material(r.get('cover_letter'))
            material_info = self.bundle_info(r, resume, letter)
            jobs.append(dict(key=key, company=r.get('company',''), title=r.get('title') or r.get('role',''),
                location=r.get('location',''), source=r.get('_source') or r.get('platform',''),
                url=safe_url(r.get('sourceUrl') or r.get('url')),
                official=safe_url(r.get('official_url') or r.get('application_url')),
                score=a.get('fit_score',r.get('fit_score')), reason=a.get('fit_reason',''),
                note=note, hints=hints, status=status, age_days=age, stale=bool(age is not None and age > STALE_DAYS), agency=agency,
                jd=r.get('text',''), letter=a.get('cover_letter_body',''),
                resume=resume.name if resume else '', cover_pdf=letter.name if letter else '',
                materials=material_info,
                ready=bool(status in ('pending','needs_qualification_review') and resume and (a.get('worth_applying') or status=='needs_qualification_review' or r.get('qualification_confirmed') or a.get('unknown_requirements')) and a.get('fit_score',0)>=70) and not (agency and age is not None and age > AGENCY_DAYS)))
        # Fresh jobs first (postings fill quickly), then by score within each group.
        jobs.sort(key=lambda j:(not j['ready'], j['stale'], j['agency'], -(j['score'] or 0), j['company']))
        return {'jobs':jobs, 'ready_count':sum(j['ready'] for j in jobs), 'email':self.email_info(), 'code_version':CODE_VERSION,
                'answers':answer_cards(self.root), 'projects':project_cards(self.root),
                'queues':len(self.queues), 'updated':datetime.now().isoformat(timespec='seconds')}

    def email_info(self):
        """Gmail reconcile status, reply-rate stats and the short review list for the page banner."""
        import gmail_reconcile as g
        g.maybe_refresh(self)
        info = {'state': g.STATUS.get('state'), 'message': g.STATUS.get('message', ''), 'review': [], 'stats': None}
        try:
            scores = {k: (r.get('analysis') or {}).get('fit_score') for k, r in self.records().items()
                      if isinstance((r.get('analysis') or {}).get('fit_score'), (int, float))}
            if self.csv_path.exists(): info['stats'] = g.stats(self.csv_path, scores)
            review = self.root / 'applications/email-review.local.json'
            if review.exists(): info['review'] = json.loads(review.read_text(encoding='utf-8'))[:20]
            m = re.search(r'Primary Application Email:\s*(\S+@\S+)', (self.root/'profile.md').read_text(encoding='utf-8'))
            info['account'] = m[1] if m else ''
        except Exception as e:
            info['message'] += f'（统计读取失败：{e}）'
        return info

    def dismiss_review(self, ids):
        review = self.root / 'applications/email-review.local.json'
        if not review.exists(): return
        items = [x for x in json.loads(review.read_text(encoding='utf-8')) if x.get('id') not in set(ids)]
        review.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')

    def bundle_info(self, row, resume, letter):
        # Read existing deterministic bundle paths without writing during GET.
        folder=bundle_folder(self.root,row)
        files={}
        for kind,source in [('resume',resume),('cover_letter',letter)]:
            if source:
                target=folder/upload_name(kind,source)
                files[kind]=str(target if target.is_file() else source)
        if (folder/'Cover_Letter.txt').is_file():files['letter_text']=str(folder/'Cover_Letter.txt')
        short=upload_folder(row)
        for kind,name in (('resume',UPLOAD_RESUME_NAME),('cover_letter',UPLOAD_COVER_NAME)):
            if kind in files and (short/name).is_file():files['upload_'+kind]=str(short/name)
        return {'folder':str(folder) if folder.is_dir() else '', 'files':files}

    def save_entry(self,key,url):
        if key not in self.records():raise ValueError('岗位不存在')
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.username or parsed.password or parsed.hostname not in ('jobs.lever.co','jobs.eu.lever.co','boards.greenhouse.io','job-boards.greenhouse.io','jobs.ashbyhq.com','apply.workable.com') or len(parsed.path.strip('/').split('/'))<2:raise ValueError('申请链接不受支持')
        self.journal.parent.mkdir(parents=True,exist_ok=True)
        event={'job_key':key,'application_url':url,'entry_verification':'source_link_requires_identity_check','at':datetime.now().astimezone().isoformat()}
        with self.journal.open('a',encoding='utf-8') as f:f.write(json.dumps(event,ensure_ascii=False)+'\n')

    def record(self, key, status, note, override_duplicate=False, verification='user_reported'):
        if status not in STATES: raise ValueError('Unknown status')
        note = str(note).strip()
        rows = self.records()
        if key not in rows: raise ValueError('Unknown job')
        r = rows[key]
        if r.get('status') == 'applied' and status != 'applied': raise ValueError('Applied record cannot be downgraded')
        if status == 'reviewed' and r.get('status') not in ('needs_qualification_review','reviewed'): raise ValueError('此岗位不是待核对状态')
        if status == 'reviewed' and not note: note='用户已核对，加入待投队列'
        if status == 'applied' and not note: note='用户手动标记已投递'
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        # Separate human attestations from browser-verified events; CSV provides existing pipeline dedup.
        event = dict(job_key=key, status=status, reason=note, verification=verification,
                     at=datetime.now().astimezone().isoformat())
        if status == 'reviewed': event['qualification_confirmed']=True
        if override_duplicate: event['duplicate_override']=True
        with self.journal.open('a', encoding='utf-8') as f: f.write(json.dumps(event,ensure_ascii=False)+'\n')
        if status != 'applied': return
        with CSV_LOCK: self._append_applied(r, note, verification)

    def _append_applied(self, r, note, verification):
        existing, fields = [], FIELDS
        if self.csv_path.exists():
            with self.csv_path.open(encoding='utf-8-sig',newline='') as f:
                reader = csv.DictReader(f); fields = reader.fieldnames; existing = list(reader)
        url = r.get('sourceUrl') or r.get('url')
        if any(job_key(x.get('Job URL','')) == job_key(url) and x.get('Status','').lower().startswith('applied') for x in existing): return
        row = dict.fromkeys(fields,'')
        row.update({'Date Applied':datetime.now().strftime('%Y-%m-%d'),'Company':r.get('company',''),
            'Position':r.get('title') or r.get('role',''),'Location':r.get('location',''),'Job URL':url,
            'Platform':r.get('_source') or r.get('platform',''),'Status':'Applied',
            'Resume Version':(self.resume_file(r) or Path(resume_value(r))).name,
            'Official URL':next((r[f] for f in OFFICIAL_FIELDS if official_key(r.get(f,''))), ''),
            'Notes':('Gmail-verified: ' if verification=='gmail' else 'User-confirmed manual application: ')+note})
        row = {k: row.get(k, '') for k in fields}  # older CSVs may lack newer columns
        new_file = not self.csv_path.exists()
        with self.csv_path.open('a',encoding='utf-8',newline='') as f:
            writer = csv.DictWriter(f,fieldnames=fields)
            if new_file: writer.writeheader()
            writer.writerow(row)


def handler(workbench, token):
    from assisted_manager import AssistedManager
    assistant = AssistedManager(workbench)
    from discovery_manager import DiscoveryManager
    discovery = DiscoveryManager(workbench)
    from jobsearch_bridge import JobSearchBridge
    bridge = JobSearchBridge(workbench,discovery)
    class Handler(BaseHTTPRequestHandler):
        services = (bridge, discovery)
        def log_message(self, *args): pass

        def reply(self, status, body, kind='application/json; charset=utf-8'):
            raw = body if isinstance(body, bytes) else json.dumps(body,ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers(); self.wfile.write(raw)

        def valid_host(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def do_GET(self):
            if not self.valid_host(): return self.reply(403, {'error':'Invalid host'})
            p = urlsplit(self.path)
            try:
                if p.path == '/':
                    html = Path(__file__).with_name('manual_workbench.html').read_text(encoding='utf-8')
                    return self.reply(200,html.replace('__TOKEN__',token).encode('utf-8'),'text/html; charset=utf-8')
                if p.path == '/api/session':
                    origin=f'http://127.0.0.1:{self.server.server_port}'
                    if self.headers.get('X-Workbench-Session')!='refresh' or self.headers.get('Origin') not in (None,origin):
                        return self.reply(403,{'error':'请求来源不允许','code':'invalid_origin'})
                    return self.reply(200,{'token':token})
                if p.path == '/api/jobs': return self.reply(200,workbench.data())
                if p.path == '/api/assist': return self.reply(200,assistant.status())
                if p.path == '/api/discovery': return self.reply(200,bridge.status())
                if p.path == '/reference':
                    name = parse_qs(p.query).get('name',[''])[0]
                    if name not in ('profile.md','job-application-answer-bank.md'): return self.reply(404,{})
                    return self.reply(200,(workbench.root/name).read_bytes(),'text/plain; charset=utf-8')
                if p.path == '/material':
                    q = parse_qs(p.query); r = workbench.records().get(q.get('key',[''])[0],{})
                    kind = q.get('kind',[''])[0]
                    if kind not in ('resume','cover_letter'): return self.reply(404,{})
                    path = workbench.resume_file(r) if kind=='resume' else workbench.material(r.get(kind, ''))
                    if not path: return self.reply(404,{'error':'Material missing'})
                    mime = 'application/pdf' if path.suffix.lower()=='.pdf' else 'application/octet-stream'
                    return self.reply(200,path.read_bytes(),mime)
                return self.reply(404,{})
            except Exception as e: return self.reply(500,{'error':str(e)})

        def do_POST(self):
            origin = f'http://127.0.0.1:{self.server.server_port}'
            if not self.valid_host() or self.headers.get('Origin') != origin:
                return self.reply(403,{'error':'请通过 http://127.0.0.1:'+str(self.server.server_port)+'/ 打开工作台','code':'invalid_origin'})
            if self.headers.get('X-Workbench-Token') != token:
                return self.reply(403,{'error':'工作台连接已更新，请刷新页面后再试','code':'session_expired'})
            if self.path == '/api/jobsearch/open':
                try:
                    from runtime_config import open_jobsearch_workbench
                    return self.reply(200,open_jobsearch_workbench())
                except Exception as e:return self.reply(400,{'error':str(e)})
            if self.path == '/api/materials':
                try: return self.reply(200,{'prepared':len(workbench.sync_materials())})
                except Exception as e:return self.reply(500,{'error':str(e)})
            if self.path == '/api/email-review/dismiss':
                try:
                    size = int(self.headers.get('Content-Length','0'))
                    if not 0 < size <= 16000: raise ValueError('Invalid request length')
                    workbench.dismiss_review(json.loads(self.rfile.read(size)).get('ids', []))
                    return self.reply(200,{'saved':True})
                except (ValueError,KeyError) as e: return self.reply(400,{'error':str(e)})
            if self.path not in ('/api/entry','/api/codex-handoff','/api/status','/api/assist','/api/discovery','/api/bridge/poll','/api/bridge/result','/api/bridge/progress'): return self.reply(404,{})
            try:
                size = int(self.headers.get('Content-Length','0'))
                if not 0 < size <= (1200000 if self.path=='/api/bridge/result' else 16000): raise ValueError('Invalid request length')
                d = json.loads(self.rfile.read(size))
                if self.path == '/api/bridge/poll': return self.reply(200,bridge.poll(d.get('client')))
                if self.path == '/api/bridge/progress': return self.reply(200,bridge.progress(d))
                if self.path == '/api/bridge/result': return self.reply(200,bridge.complete(d))
                if self.path == '/api/discovery': return self.reply(200,bridge.start(d))
                if self.path == '/api/entry':
                    workbench.save_entry(d['key'],d['application_url'])
                    return self.reply(200,{'saved':True})
                if self.path == '/api/codex-handoff':
                    from codex_handoff import prepare_handoff
                    return self.reply(200,prepare_handoff(workbench,d['key'],d.get('application_url','')))
                if self.path == '/api/assist': return self.reply(200,assistant.start(d['key'],d.get('application_url')))
                workbench.record(d['key'], d['status'], d.get('note',''), bool(d.get('override_duplicate')))
                return self.reply(200,{'saved':True})
            except (ValueError,KeyError) as e: return self.reply(400,{'error':str(e)})
            except Exception as e: return self.reply(500,{'error':str(e)})
    return Handler


class WorkbenchServer(HTTPServer):
    # Windows SO_REUSEADDR can let old and new servers bind the same port.
    allow_reuse_address = False
    def server_bind(self):
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queue',action='append',help='Prepared queue.json; repeat for multiple batches')
    parser.add_argument('--port',type=int,default=8765)
    args = parser.parse_args()
    queues = [Path(p) for p in args.queue] if args.queue else sorted((ROOT/'tmp').glob('**/queue.json'))
    workbench = Workbench(ROOT,queues,discover=not args.queue)
    workbench.sync_materials()
    workbench.data()  # fail before serving if persisted input is damaged
    from gmail_reconcile import start_background
    start_background(workbench)  # read-only Gmail check; never blocks the page
    handler_class = handler(workbench,secrets.token_urlsafe(32))
    server = WorkbenchServer(('127.0.0.1',args.port),handler_class)
    from codex_rounds import RoundWorker
    RoundWorker(workbench, handler_class.services[0]).start()  # Codex's continuous rounds (file requests)
    print(f'Manual workbench: http://127.0.0.1:{server.server_port} | queues={len(queues)}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__': main()
