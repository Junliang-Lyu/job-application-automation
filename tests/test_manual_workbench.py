import csv
import json
import sys
import tempfile
import unittest
import threading
from http.server import HTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from manual_workbench import Workbench, safe_url, handler
from workbench_materials import answer_cards


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'run').mkdir()
        self.queue = self.root / 'run/queue.json'
        self.queue.write_text(json.dumps([{'sourceUrl': 'https://www.linkedin.com/jobs/view/123/',
            'company': '<script>alert(1)</script>', 'title': 'Engineer', 'status': 'ready_for_review',
            'analysis': {'fit_score': 80, 'fit_reason': 'Good', 'cover_letter_body': 'Letter'},
            'resume': '../outside.pdf'}]), encoding='utf-8')
        self.w = Workbench(self.root, [self.queue])

    def test_urls_and_materials(self):
        self.assertEqual(safe_url('javascript:alert(1)'), '')
        self.assertEqual(safe_url('https://user:secret@example.com'), '')
        self.assertIsNone(self.w.material('../outside.pdf'))
        self.assertIsNone(self.w.material('profile.md'))

    def test_duplicate_and_history_exclusion(self):
        rows = json.loads(self.queue.read_text())
        self.queue.write_text(json.dumps(rows + rows), encoding='utf-8')
        self.assertEqual(len(self.w.data()['jobs']), 1)
        self.w.record('linkedin:123', 'applied', 'Saw successful application confirmation')
        self.assertEqual(self.w.data()['jobs'][0]['status'], 'applied')
        self.assertEqual(self.w.data()['ready_count'], 0)

    def test_manual_qualification_review_keeps_candidate_in_ready_queue(self):
        rows=json.loads(self.queue.read_text(encoding='utf-8'))
        (self.root/'resume.pdf').write_bytes(b'%PDF-1.4 fixture')
        rows[0]['resume']='resume.pdf'
        rows[0]['status']='needs_qualification_review'
        rows[0]['analysis']['worth_applying']=False
        self.queue.write_text(json.dumps(rows),encoding='utf-8')
        self.w.record('linkedin:123','reviewed','')
        again=Workbench(self.root,[self.queue]);job=again.data()['jobs'][0]
        self.assertEqual(job['status'],'pending');self.assertTrue(job['ready'])
        self.assertTrue(again.records()['linkedin:123']['qualification_confirmed'])
        self.assertFalse(self.w.csv_path.exists())

    def test_saved_entry_and_codex_handoff_preserve_status(self):
        from codex_handoff import prepare_handoff
        url='https://jobs.lever.co/acme/test/apply'
        self.w.save_entry('linkedin:123',url)
        self.assertEqual(self.w.data()['jobs'][0]['official'],url)
        result=prepare_handoff(self.w,'linkedin:123')
        task=Path(result['path']).read_text(encoding='utf-8')
        self.assertIn('绝不点击最终提交',task)
        self.assertIn('提交 1、3',task)
        self.assertIn('绝不重试提交',task)
        self.assertIn('填到提交前',task)
        self.assertIn('mark.py --key',task)
        self.assertIn('Standing Form-Filling Authorization',task)
        self.assertIn('Current Status',task)
        self.assertNotIn('config/batch-search.json',task)
        self.assertIn('你才提交',result['prompt'])
        context=json.loads(Path(result['path']).with_name('context.json').read_text(encoding='utf-8'))
        self.assertEqual(context['start_job']['application_url'],url)
        self.assertEqual(context['queue'][0]['number'],1)
        self.assertEqual(self.w.records()['linkedin:123']['status'],'ready_for_review')
        with self.assertRaises(ValueError):self.w.save_entry('linkedin:123','https://evil.example/apply')
        self.w.record('linkedin:123','applied','')
        with self.assertRaises(ValueError):prepare_handoff(self.w,'linkedin:123')

    def test_applied_requires_attestation_and_is_idempotent(self):
        self.w.record('linkedin:123', 'applied', '')
        self.assertEqual(self.w.data()['ready_count'],0)
        self.w.record('linkedin:123', 'applied', 'Success page shown')
        self.w.record('linkedin:123', 'applied', 'Success page shown')
        with self.w.csv_path.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertIn('User-confirmed', rows[0]['Notes'])
        with self.assertRaises(ValueError): self.w.record('linkedin:123', 'pending', '')

    def test_persists_uncertain_without_marking_applied(self):
        self.w.record('linkedin:123', 'submission_unknown', 'No confirmation')
        again = Workbench(self.root, [self.queue])
        self.assertEqual(again.data()['jobs'][0]['status'], 'submission_unknown')
        self.assertFalse(self.w.csv_path.exists())

    def test_http_rejects_cross_origin_and_saves_authorized_request(self):
        server=HTTPServer(('127.0.0.1',0),handler(self.w,'test-token'))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        data=json.dumps({'key':'linkedin:123','status':'skipped','note':'Not suitable'}).encode()
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(base+'/api/status',data=data,headers={'Origin':'https://evil.example'}))
        self.assertEqual(error.exception.code,403)
        error.exception.close()
        self.assertFalse(self.w.journal.exists())
        with urlopen(Request(base+'/api/status',data=data,headers={'Origin':base,'X-Workbench-Token':'test-token'})) as r:
            self.assertTrue(json.load(r)['saved'])
        self.assertEqual(self.w.data()['jobs'][0]['status'],'skipped')

    def test_missing_files_and_unsafe_official_link(self):
        r=json.loads(self.queue.read_text())[0]
        r['official_url']='javascript:alert(1)'
        self.queue.write_text(json.dumps([r]),encoding='utf-8')
        j=self.w.data()['jobs'][0]
        self.assertEqual(j['official'],'')
        self.assertFalse(j['ready'])

    def test_answer_cards_retain_authorization_caveat(self):
        (self.root/'profile.md').write_text('Name: Example\nPrimary Application Email: test@example.com (Gmail)\nFuture Employer Sponsorship Required: Yes\nEarliest Start Date:\nDecember 15, 2026, subject to OPT work authorization\nPaid software-engineering experience after undergraduate graduation: 1–2 years',encoding='utf-8')
        cards=[c for g in answer_cards(self.root) for c in g['cards']]
        self.assertIn('test@example.com',[c['value'] for c in cards])
        self.assertTrue(next(c for c in cards if c['label']=='最早到岗')['warning'])
        self.assertFalse(any('1–2' in c['value'] for c in cards))

    def test_bundle_files_local_and_stable(self):
        (self.root/'resume').mkdir()
        (self.root/'resume/main.pdf').write_bytes(b'%PDF-test')
        rows=json.loads(self.queue.read_text());rows[0]['resume']='resume/main.pdf'
        self.queue.write_text(json.dumps(rows),encoding='utf-8')
        result=self.w.sync_materials()['linkedin:123']
        folder=Path(result['folder'])
        self.assertTrue(folder.is_relative_to(self.root/'applications/materials'))
        self.assertEqual(Path(result['files']['resume']).read_bytes(),b'%PDF-test')
        self.assertEqual((folder/'Cover_Letter.txt').read_text(),'Letter')
        stamp=(folder/'Cover_Letter.txt').stat().st_mtime_ns
        self.w.sync_materials()
        self.assertEqual((folder/'Cover_Letter.txt').stat().st_mtime_ns,stamp)

    def test_new_queue_discovered_after_start(self):
        (self.root/'tmp/new').mkdir(parents=True)
        w=Workbench(self.root,[],discover=True)
        self.assertEqual(w.data()['jobs'],[])
        (self.root/'tmp/new/queue.json').write_text(self.queue.read_text(),encoding='utf-8')
        self.assertEqual(len(w.data()['jobs']),1)


if __name__ == '__main__': unittest.main()
