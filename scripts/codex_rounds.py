"""File-based round requests from Codex to the running workbench (2026-11-15).

Codex's shell may not reach localhost, so the CLI (scripts/codex_round.py) drops a request file in
tmp/codex-rounds/ and this worker, running inside the workbench process, answers it:

  check  - ask the Job Search extension whether each job in a handoff is still open; closed ones are
           marked skipped and flagged in context.json, so Codex never fills them.
  next   - when a round is done: search again if few ready jobs remain, check the new ones are open,
           and write the next handoff (numbering continues). Never submits anything.
"""
import json
import threading
import time
from datetime import datetime
from pathlib import Path

from discovery_manager import ACTIVE

MIN_READY = 3          # fewer ready jobs than this -> run one search first
SEARCH_TIMEOUT = 900   # seconds for extension search + DeepSeek preparation
CHECK_TIMEOUT = 120   # 4-8 pages take about a minute; longer means the extension never got the task
BUSY = set(ACTIVE) | {'searching', 'checking'}
SEARCH = {'keywords': '', 'location': 'United States', 'limit': 10, 'source': 'both'}  # '' = rotating keyword list


def rounds_dir(root):
    return Path(root) / 'tmp/codex-rounds'


class RoundWorker:
    def __init__(self, workbench, bridge, sleep=time.sleep, clock=time.monotonic):
        self.workbench, self.bridge, self.sleep, self.clock = workbench, bridge, sleep, clock
        self.folder = rounds_dir(workbench.root)

    def start(self):
        threading.Thread(target=self.heartbeat, daemon=True, name='codex-rounds-alive').start()
        threading.Thread(target=self.loop, daemon=True, name='codex-rounds').start()

    def beat(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / 'worker.alive').write_text(datetime.now().astimezone().isoformat(), encoding='utf-8')

    def heartbeat(self):
        # Separate thread: a 10-minute search must not make the CLI think the workbench is gone.
        while True:
            try:
                self.beat()
            except OSError:
                pass
            time.sleep(5)

    def loop(self):
        while True:
            try:
                self.tick()
            except Exception:
                pass
            self.sleep(2)

    def tick(self):
        self.beat()
        for request in sorted(self.folder.glob('*.request.json')):
            response = request.with_name(request.name.replace('.request.json', '.response.json'))
            if response.exists():
                continue
            try:
                data = json.loads(request.read_text(encoding='utf-8'))
                result = self.handle(data)
            except Exception as exc:
                result = {'ok': False, 'error': str(exc)}
            tmp = response.with_suffix('.tmp')
            tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(response)

    # ---- actions -------------------------------------------------------------------------------
    def handle(self, data):
        if data.get('action') == 'check':
            return self.check_context(Path(data['context']))
        if data.get('action') == 'next':
            return self.next_round(Path(data['context']))
        raise ValueError('未知请求')

    def wait_idle(self, timeout):
        start = self.clock()
        self.sleep(3)
        while self.clock() - start < timeout:
            if self.bridge.status().get('status') not in BUSY:
                return True
            self.sleep(3)
        return False

    def check_keys(self, keys):
        """Return (closed, unknown, error). Closed jobs are recorded as skipped."""
        jobs = {j['key']: j for j in self.workbench.data()['jobs']}
        urls = {jobs[k]['url']: k for k in keys if k in jobs and jobs[k].get('url')}
        if not urls:
            return [], list(keys), ''
        try:
            task = self.bridge.start_check(list(urls))
        except ValueError as exc:
            return [], list(keys), str(exc)
        if not task:
            return [], list(keys), ''
        start = self.clock()
        while self.clock() - start < CHECK_TIMEOUT:
            result = getattr(self.bridge, 'check_result', None)
            if result and result.get('id') == task:
                break
            self.sleep(2)
        else:
            cancel = getattr(self.bridge, 'cancel', None)
            if cancel:
                cancel(task)
            return [], list(keys), '扩展没有响应。重新加载扩展后，必须刷新一次 Job Search 里的工作台标签页'
        closed, unknown = [], []
        states = {r['url']: r['state'] for r in result['results']}
        for url, key in urls.items():
            state = states.get(url, 'unknown')
            if state == 'closed':
                try:
                    self.workbench.record(key, 'skipped', '扩展检测：岗位已不再接受申请', verification='extension_check')
                except ValueError:
                    pass
                closed.append(key)
            elif state != 'open':
                unknown.append(key)
        return closed, unknown, result.get('error', '')

    def check_context(self, path):
        context = json.loads(path.read_text(encoding='utf-8'))
        keys = [q['job_key'] for q in context['queue'] if not q.get('closed')]
        closed, unknown, error = self.check_keys(keys)
        for q in context['queue']:
            if q['job_key'] in closed:
                q['closed'] = True
        context['open_check'] = {'at': datetime.now().astimezone().isoformat(), 'closed': closed, 'unknown': unknown, 'error': error}
        path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding='utf-8')
        numbers = [q['number'] for q in context['queue'] if q['job_key'] in closed]
        msg = f"已关闭 {len(closed)} 个（编号 {', '.join(map(str, numbers)) or '无'}，已自动跳过）；无法判断 {len(unknown)} 个，打开页面时自己确认"
        if error:
            msg += f'；检查未完成：{error}'
        return {'ok': True, 'closed_numbers': numbers, 'unknown': len(unknown), 'message': msg}

    def ready_keys(self, exclude):
        return [j['key'] for j in self.workbench.data()['jobs']
                if j.get('ready') and j['status'] in ('pending', 'needs_qualification_review') and j['key'] not in exclude]

    def next_round(self, path):
        from codex_handoff import build_handoff, MAX_JOBS
        previous = json.loads(path.read_text(encoding='utf-8'))
        exclude = set(previous.get('previous_keys', [])) | {q['job_key'] for q in previous['queue']}
        start_number = max([q['number'] for q in previous['queue']] + [0]) + 1
        searched, notes = False, []
        candidates = self.ready_keys(exclude)
        if len(candidates) < MIN_READY:
            try:
                self.bridge.start(dict(SEARCH))
                searched = True
                if not self.wait_idle(SEARCH_TIMEOUT):
                    notes.append('搜岗超时')
                notes.append(self.bridge.status().get('message', ''))
            except ValueError as exc:
                notes.append(f'没能搜岗：{exc}')
            candidates = self.ready_keys(exclude)
        closed = []
        if candidates:
            closed, _, error = self.check_keys(candidates[:MAX_JOBS + 6])
            if error:
                notes.append(f'开放检查未完成：{error}')
            candidates = [k for k in candidates if k not in closed]
        if not candidates:
            return {'ok': True, 'count': 0, 'searched': searched, 'closed': len(closed),
                    'message': '没有新的可投岗位了。' + '；'.join(n for n in notes if n)}
        result = build_handoff(self.workbench, candidates[:MAX_JOBS], start_number=start_number,
                               previous_keys=sorted(exclude), checked=True)
        result.update(ok=True, searched=searched, closed=len(closed),
                      message=f"第 {start_number}–{start_number + result['count'] - 1} 号，共 {result['count']} 个岗位"
                              + (f"（先搜了一轮；{'；'.join(n for n in notes if n)}）" if searched else '')
                              + (f"；{len(closed)} 个已关闭的岗位已跳过" if closed else ''))
        return result
