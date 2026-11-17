"""Continuous Codex rounds, open checks, hard pre-filters, retries and upload copies (2026-11-15)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import analyze_jds  # noqa: E402
from qualification import hard_conflicts, looks_like_agency  # noqa: E402
from prepare_batch import prepare_candidates  # noqa: E402
from jobsearch_bridge import JobSearchBridge  # noqa: E402
from codex_rounds import RoundWorker  # noqa: E402
from manual_workbench import resume_value  # noqa: E402
import workbench_materials  # noqa: E402


class _Resp:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return json.dumps(self.body).encode()


class RetryTests(unittest.TestCase):
    def test_transient_network_errors_are_retried(self):
        calls, waits = [], []
        def opener():
            calls.append(1)
            if len(calls) < 3: raise URLError('getaddrinfo failed')
            return _Resp({'ok': 1})
        self.assertEqual(analyze_jds._call_with_retry(opener, sleep=waits.append), {'ok': 1})
        self.assertEqual(waits, [5, 15])

    def test_other_errors_fail_at_once(self):
        waits = []
        def opener(): raise ValueError('bad json')
        with self.assertRaises(ValueError): analyze_jds._call_with_retry(opener, sleep=waits.append)
        self.assertEqual(waits, [])

    def test_gives_up_after_last_wait(self):
        def opener(): raise URLError('down')
        with self.assertRaises(URLError): analyze_jds._call_with_retry(opener, waits=(1,), sleep=lambda s: None)


class HardFilterTests(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(hard_conflicts({'min_gpa_required': 3.0}), [])
        self.assertEqual(len(hard_conflicts({'min_gpa_required': 3.8})), 1)
        self.assertEqual(len(hard_conflicts({'starts_before_available': True, 'contract_only': True})), 2)
        self.assertEqual(hard_conflicts({'min_gpa_required': True}), [])  # bool is not a GPA

    def test_agency(self):
        self.assertTrue(looks_like_agency({'staffing_agency': True}))
        self.assertFalse(looks_like_agency({'staffing_agency': False}, 'our client'))
        self.assertTrue(looks_like_agency({}, 'We are hiring on behalf of our client, a fintech'))
        self.assertFalse(looks_like_agency({}, 'Join our team at Plaid'))

    def test_prepare_marks_not_recommended_and_agency(self):
        rec = dict(sourceUrl='https://www.linkedin.com/jobs/view/42/', title='Data Analyst', company='Talent Co', text='x' * 300)
        analysis = dict(fit_score=85, worth_applying=True, unknown_requirements=[], confirmed_conflicts=[],
                        resume_track='data', min_gpa_required=3.8, staffing_agency=True, cover_letter_body='Hi')
        out = prepare_candidates([rec], lambda r: dict(analysis), lambda r, a: 'x.pdf')[0]
        self.assertEqual(out['status'], 'not_recommended')
        self.assertIn('GPA', out['reason'])
        self.assertTrue(out['agency'])


@unittest.skipUnless((Path(__file__).resolve().parents[1] / 'resume/versions/Junliang_Lyu_Resume_SWE.pdf').is_file(), 'private resume files not present')
class ResumeFallbackTests(unittest.TestCase):
    def test_old_main_resume_is_replaced_by_track_or_title(self):
        self.assertTrue(resume_value({'resume': 'resume/main/Junliang_Lyu_Main_Resume_v5_1.pdf', 'title': 'Data Analyst'}).endswith('Resume_Data.pdf'))
        self.assertTrue(resume_value({'title': 'Software Engineer I, Backend'}).endswith('Resume_SWE.pdf'))
        self.assertTrue(resume_value({'title': 'Applications Systems Analyst'}).endswith('Resume_IT_Analyst.pdf'))
        self.assertTrue(resume_value({'analysis': {'resume_track': 'ai'}, 'title': 'Engineer'}).endswith('Resume_AI.pdf'))
        self.assertEqual(resume_value({'resume': 'custom/x.pdf'}), 'custom/x.pdf')


class UploadCopyTests(unittest.TestCase):
    def test_short_ascii_copies(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as up:
            root = Path(d); (root / 'r.pdf').write_bytes(b'%PDF-1 resume'); (root / 'c.pdf').write_bytes(b'%PDF-1 letter')
            old = os.environ.get('JOB_UPLOAD_DIR'); os.environ['JOB_UPLOAD_DIR'] = up
            try:
                files = workbench_materials.bundle(root, {'job_key': 'linkedin:1', 'company': '公司', 'title': 'Dev'},
                                                   root / 'r.pdf', root / 'c.pdf')['files']
            finally:
                if old is None: os.environ.pop('JOB_UPLOAD_DIR')
                else: os.environ['JOB_UPLOAD_DIR'] = old
            self.assertTrue(files['upload_resume'].startswith(up) and files['upload_resume'].endswith('Junliang_Lyu_Resume.pdf'))
            self.assertEqual(Path(files['upload_cover_letter']).read_bytes(), b'%PDF-1 letter')
            self.assertTrue(files['upload_cover_letter'].isascii())


class BridgeCheckTests(unittest.TestCase):
    def test_check_task_round_trip(self):
        desk = Mock(); desk.records.return_value = {}; manager = Mock(); manager.status.return_value = {'status': 'idle'}
        b = JobSearchBridge(desk, manager); b.poll('a')
        tid = b.start_check(['https://www.linkedin.com/jobs/view/1/', 'https://evil.com/x'])
        task = b.poll('a')['task']
        self.assertEqual(task['kind'], 'check'); self.assertEqual(task['urls'], ['https://www.linkedin.com/jobs/view/1/'])
        self.assertEqual(b.status()['status'], 'checking')
        with self.assertRaises(ValueError): b.start({'keywords': 'x'})  # one task at a time
        b.complete({'id': tid, 'client': 'a', 'check_results': [{'url': 'https://www.linkedin.com/jobs/view/1/', 'state': 'closed'},
                                                                {'url': 'https://other/', 'state': 'closed'}]})
        self.assertEqual(b.check_result['results'], [{'url': 'https://www.linkedin.com/jobs/view/1/', 'state': 'closed'}])
        self.assertIsNone(b.task)

    def test_check_needs_connection(self):
        desk = Mock(); manager = Mock(); manager.status.return_value = {'status': 'idle'}
        with self.assertRaises(ValueError): JobSearchBridge(desk, manager).start_check(['https://www.linkedin.com/jobs/view/1/'])


class FakeBridge:
    def __init__(self, states, after_search=None):
        self.states, self.after_search, self.check_result, self.searches = states, after_search, None, 0
    def start_check(self, urls):
        self.check_result = {'id': 't', 'error': '', 'results': [{'url': u, 'state': self.states.get(u, 'open')} for u in urls]}
        return 't'
    def start(self, data):
        self.searches += 1
        if self.after_search: self.after_search()
    def status(self): return {'status': 'complete', 'message': '取得 3 份 JD'}


class FakeDesk:
    def __init__(self, root, jobs):
        self.root, self.jobs, self.recorded = Path(root), jobs, []
    def data(self): return {'jobs': [j for j in self.jobs if j['key'] not in {k for k, *_ in self.recorded}]}
    def records(self): return {j['key']: {} for j in self.jobs}
    def record(self, key, status, note, verification=''): self.recorded.append((key, status, verification))


def job(n, ready=True):
    return dict(key=f'linkedin:{n}', company=f'Co{n}', title='Engineer', location='TX', status='pending', score=80,
                url=f'https://www.linkedin.com/jobs/view/{n}/', official='', note='', ready=ready, age_days=1, agency=False,
                materials={'files': {'resume': f'/long/{n}/r.pdf', 'upload_resume': f'/u/{n}/Junliang_Lyu_Resume.pdf'}})


class RoundWorkerTests(unittest.TestCase):
    def test_check_marks_closed_in_context(self):
        with tempfile.TemporaryDirectory() as d:
            desk = FakeDesk(d, [job(1), job(2)])
            ctx = Path(d) / 'context.json'
            ctx.write_text(json.dumps({'queue': [{'job_key': 'linkedin:1', 'number': 1}, {'job_key': 'linkedin:2', 'number': 2}]}), encoding='utf-8')
            w = RoundWorker(desk, FakeBridge({'https://www.linkedin.com/jobs/view/2/': 'closed'}), sleep=lambda s: None)
            out = w.handle({'action': 'check', 'context': str(ctx)})
            self.assertEqual(out['closed_numbers'], [2])
            saved = json.loads(ctx.read_text(encoding='utf-8'))
            self.assertTrue(saved['queue'][1]['closed']); self.assertNotIn('closed', saved['queue'][0])
            self.assertEqual(desk.recorded, [('linkedin:2', 'skipped', 'extension_check')])

    def test_next_searches_when_short_and_continues_numbering(self):
        with tempfile.TemporaryDirectory() as d:
            desk = FakeDesk(d, [job(1)])
            def found(): desk.jobs += [job(5), job(6), job(7)]
            bridge = FakeBridge({'https://www.linkedin.com/jobs/view/6/': 'closed'}, after_search=found)
            ctx = Path(d) / 'context.json'
            ctx.write_text(json.dumps({'queue': [{'job_key': 'linkedin:1', 'number': 1}, {'job_key': 'linkedin:9', 'number': 2}]}), encoding='utf-8')
            out = RoundWorker(desk, bridge, sleep=lambda s: None, clock=iter(range(0, 10000, 5)).__next__).handle({'action': 'next', 'context': str(ctx)})
            self.assertEqual(bridge.searches, 1)
            self.assertEqual(out['count'], 2)
            new = json.loads(Path(out['context']).read_text(encoding='utf-8'))
            self.assertEqual([q['number'] for q in new['queue']], [3, 4])
            self.assertEqual([q['job_key'] for q in new['queue']], ['linkedin:5', 'linkedin:7'])
            self.assertEqual(set(new['previous_keys']), {'linkedin:1', 'linkedin:9'})
            self.assertEqual(new['queue'][0]['upload_files']['resume'], '/u/5/Junliang_Lyu_Resume.pdf')
            task = Path(out['path']).read_text(encoding='utf-8')
            self.assertIn('编号 3–4', task); self.assertIn('codex_round.py next', task)

    def test_next_reports_nothing_left(self):
        with tempfile.TemporaryDirectory() as d:
            desk = FakeDesk(d, [job(1)])
            ctx = Path(d) / 'context.json'
            ctx.write_text(json.dumps({'queue': [{'job_key': 'linkedin:1', 'number': 1}]}), encoding='utf-8')
            out = RoundWorker(desk, FakeBridge({}), sleep=lambda s: None, clock=iter(range(0, 10000, 5)).__next__).handle({'action': 'next', 'context': str(ctx)})
            self.assertEqual(out['count'], 0)

    def test_check_gives_up_and_frees_bridge_when_extension_is_silent(self):
        with tempfile.TemporaryDirectory() as d:
            desk = FakeDesk(d, [job(1)])
            bridge = FakeBridge({}); bridge.start_check = lambda urls: 'silent'; bridge.cancel = Mock()
            w = RoundWorker(desk, bridge, sleep=lambda s: None, clock=iter(range(0, 10000, 30)).__next__)
            closed, unknown, error = w.check_keys(['linkedin:1'])
            self.assertEqual((closed, unknown), ([], ['linkedin:1']))
            self.assertIn('刷新', error); bridge.cancel.assert_called_once_with('silent')

    def test_agency_name_list(self):
        self.assertTrue(looks_like_agency({'staffing_agency': False}, 'Join us', 'Jobgether'))
        self.assertTrue(looks_like_agency({}, '', 'TalentHop'))

    def test_tick_answers_request_files(self):
        with tempfile.TemporaryDirectory() as d:
            desk = FakeDesk(d, [])
            w = RoundWorker(desk, FakeBridge({}), sleep=lambda s: None)
            w.folder.mkdir(parents=True)
            (w.folder / 'r1.request.json').write_text(json.dumps({'action': 'nope'}), encoding='utf-8')
            w.tick()
            self.assertFalse(json.loads((w.folder / 'r1.response.json').read_text(encoding='utf-8'))['ok'])
            self.assertTrue((w.folder / 'worker.alive').exists())


if __name__ == '__main__':
    unittest.main()
