import sys
import tempfile
import unittest
import json
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import job_identity as jobs
import search_rotation as search


class JobIdentityTests(unittest.TestCase):
    def test_queue_excludes_applied_alias_and_preserves_existing_progress(self):
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'build_apply_queue.py'
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'applications').mkdir()
            (root / 'applications/applications.csv').write_text(
                'Job URL,Status\nhttps://boards.greenhouse.io/scaleai/jobs/1,Applied\n', encoding='utf-8')
            records = [{'sourceUrl': f'https://job-boards.greenhouse.io/scaleai/jobs/{i}?gh_src=x',
                        'title': 'Software Engineer', 'analysis': {'fit_score': 80, 'worth_applying': True}}
                       for i in (1, 2, 2, 3)]
            (root / 'input.json').write_text(json.dumps(records), encoding='utf-8')
            existing = {'url': 'https://boards.greenhouse.io/scaleai/jobs/2', 'status': 'in_progress'}
            (root / 'queue.json').write_text(json.dumps([existing]), encoding='utf-8')
            run = subprocess.run([sys.executable, '-X', 'utf8', str(script), '--analyzed', 'input.json', '--output', 'queue.json'],
                                 cwd=root, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr.decode('utf-8', errors='replace'))
            queue = json.loads((root / 'queue.json').read_text(encoding='utf-8'))
            self.assertEqual(len(queue), 2)
            self.assertEqual(queue[0], existing)
            self.assertTrue(queue[1]['requires_related_job_review'])

    def test_greenhouse_aliases_and_tracking(self):
        a = 'https://boards.greenhouse.io/scaleai/jobs/4730836005'
        b = 'https://job-boards.greenhouse.io/scaleai/jobs/4730836005?gh_src=abc'
        self.assertEqual(jobs.job_key(a), jobs.job_key(b))
        self.assertEqual(jobs.job_key(a), jobs.job_key('https://scale.com/careers?gh_jid=4730836005'))
        self.assertNotEqual(jobs.job_key(a), jobs.job_key(a.replace('4730836005', '4722512005')))

    def test_linkedin_and_ashby_aliases(self):
        self.assertEqual(jobs.job_key('https://www.linkedin.com/jobs/view/123/?trackingId=x'),
                         jobs.job_key('https://www.linkedin.com/jobs/search/?currentJobId=123'))
        self.assertEqual(jobs.job_key('https://jobs.ashbyhq.com/acme/abc/application'),
                         jobs.job_key('https://jobs.ashbyhq.com/acme/abc?utm_source=li'))

    def test_unknown_job_query_is_preserved(self):
        self.assertNotEqual(jobs.job_key('https://example.org/job?id=1'),
                            jobs.job_key('https://example.org/job?id=2'))

    def test_handshake_school_and_route_aliases_share_identity(self):
        self.assertEqual(jobs.job_key('https://smu.joinhandshake.com/job-search/11445105?locationFilter=x'),
                         jobs.job_key('https://app.joinhandshake.com/stu/jobs/11445105'))

    def test_submitted_excluded_before_limit_and_distinct_ids_kept(self):
        rows = [{'sourceUrl': f'https://job-boards.greenhouse.io/scaleai/jobs/{i}?gh_src=x',
                 'title': 'Software Engineer – New Grad', 'contentHash': 'same'} for i in (1, 2, 3)]
        result = jobs.prepare_records(rows, {'https://boards.greenhouse.io/scaleai/jobs/1'})
        self.assertEqual(len(result[:2]), 2)
        self.assertTrue(result[0]['sourceUrl'].split('?')[0].endswith('/2'))
        self.assertEqual(result[0]['related_job_urls'], [rows[2]['sourceUrl']])

    def test_no_company_does_not_group_unrelated_roles(self):
        result = jobs.prepare_records([{'sourceUrl': f'https://example.org/{i}', 'title': 'Engineer'} for i in (1, 2)])
        self.assertEqual(result[0]['related_job_urls'], [])

    def test_rotation_advances_only_when_search_attempt_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / 'state.json'
            queries = ['backend', 'AI', 'data']
            self.assertEqual(search.ordered_queries(queries, state, 'easy'), queries)
            self.assertFalse(state.exists())
            search.record_attempt(queries, state, 'easy', 'backend')
            self.assertEqual(search.ordered_queries(queries, state, 'easy'), ['AI', 'data', 'backend'])
            self.assertEqual(search.ordered_queries(queries, state, 'all'), queries)


if __name__ == '__main__':
    unittest.main()
