"""Official ATS posting ID dedup (2026-10-26)."""
import csv
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from job_identity import official_key, record_official_key, applied_official_keys  # noqa: E402
from manual_workbench import Workbench, FIELDS  # noqa: E402
from prepare_batch import select_candidates  # noqa: E402
from jobsearch_bridge import validate_records  # noqa: E402

AFFIRM = 'https://job-boards.greenhouse.io/affirm/jobs/8017340003'


class OfficialKeyTests(unittest.TestCase):
    def test_same_posting_different_urls(self):
        same = [AFFIRM, AFFIRM + '?gh_src=689c81d53us', 'https://boards.greenhouse.io/affirm/jobs/8017340003',
                'https://www.affirm.com/careers?gh_jid=8017340003']
        self.assertEqual({official_key(u) for u in same}, {'greenhouse:8017340003'})
        self.assertEqual(official_key('https://jobs.lever.co/palantir/abc/apply'), official_key('https://jobs.lever.co/palantir/abc/?lever-origin=applied'))
        self.assertEqual(official_key('https://jobs.ashbyhq.com/quora/x1/application'), official_key('https://jobs.ashbyhq.com/quora/x1'))

    def test_non_ats_urls_have_no_official_key(self):
        for u in ['https://www.linkedin.com/jobs/view/1/', 'https://smu.joinhandshake.com/jobs/2', 'https://www.precisely.com/careers',
                  'https://imanagecom.applytojob.com/apply', 'not a url', '']:
            self.assertEqual(official_key(u), '', u)

    def test_record_key_uses_any_known_field(self):
        self.assertEqual(record_official_key({'sourceUrl': 'https://www.linkedin.com/jobs/view/9/', 'official': AFFIRM}), 'greenhouse:8017340003')
        self.assertEqual(record_official_key({'sourceUrl': 'https://www.linkedin.com/jobs/view/9/'}), '')


class DedupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'applications').mkdir()
        (self.root / 'resume/main').mkdir(parents=True)
        (self.root / 'resume/main/Junliang_Lyu_Main_Resume_v5_1.pdf').write_bytes(b'%PDF-1.4')
        self.csv = self.root / 'applications/applications.csv'
        with open(self.csv, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerow({**dict.fromkeys(FIELDS, ''), 'Date Applied': '2026-10-26', 'Company': 'Affirm', 'Status': 'Applied',
                        'Position': 'Software Engineer I, Fullstack (Search)', 'Job URL': 'https://www.linkedin.com/jobs/view/4479198572/',
                        'Official URL': AFFIRM})

    def tearDown(self):
        self.tmp.cleanup()

    def test_prepare_batch_blocks_same_posting_even_with_new_title(self):
        config = dict(include_titles=['engineer'], exclude_titles=[], allow_unknown_location=True, locations=['UT'],
                      location_scope='us', preferred_locations=[], max_candidates=10, max_per_company=3, max_per_source=10)
        rec = dict(sourceUrl='https://www.linkedin.com/jobs/view/4479305237/', title='Full Stack Engineer (Search Platform)',
                   company='Affirm', location='Salt Lake City, UT', status='fetched', text='x' * 300,
                   application_url=AFFIRM + '?gh_src=689c81d53us')
        selected, rejected = select_candidates([rec], config, (), {}, applied_official_keys(self.csv))
        self.assertEqual(selected, [])
        self.assertEqual(rejected[0]['reason'], 'same_official_job_as_applied:2026-10-26')

    def test_workbench_marks_certain_duplicate_and_records_official_url(self):
        (self.root / 'tmp/batch-20261026').mkdir(parents=True)
        rows = [dict(sourceUrl='https://www.linkedin.com/jobs/view/4479305237/', title='Anything', company='Affirm', status='ready_for_review',
                     official=AFFIRM, analysis=dict(fit_score=90, worth_applying=True)),
                dict(sourceUrl='https://www.linkedin.com/jobs/view/77/', title='Backend Engineer', company='Plaid', status='ready_for_review',
                     application_url='https://jobs.lever.co/plaid/xyz', analysis=dict(fit_score=80, worth_applying=True))]
        (self.root / 'tmp/batch-20261026/queue.json').write_text(json.dumps(rows), encoding='utf-8')
        wb = Workbench(self.root, [], discover=True)
        jobs = {j['company']: j for j in wb.data()['jobs']}
        self.assertEqual(jobs['Affirm']['status'], 'duplicate_official')
        self.assertFalse(jobs['Affirm']['ready'])
        self.assertTrue(jobs['Plaid']['ready'])
        wb.record(jobs['Plaid']['key'], 'applied', '')
        with open(self.csv, encoding='utf-8-sig') as f:
            plaid = [r for r in csv.DictReader(f) if r['Company'] == 'Plaid'][0]
        self.assertEqual(plaid['Official URL'], 'https://jobs.lever.co/plaid/xyz')

    def test_old_csv_without_official_column_still_appends(self):
        old_fields = [f for f in FIELDS if f != 'Official URL']
        with open(self.csv, 'w', encoding='utf-8-sig', newline='') as f:
            csv.DictWriter(f, fieldnames=old_fields).writeheader()
        (self.root / 'tmp/batch-20261026').mkdir(parents=True)
        (self.root / 'tmp/batch-20261026/queue.json').write_text(json.dumps([dict(
            sourceUrl='https://www.linkedin.com/jobs/view/5/', title='Engineer', company='Acme', status='ready_for_review',
            application_url='https://jobs.lever.co/acme/1', analysis=dict(fit_score=80, worth_applying=True))]), encoding='utf-8')
        wb = Workbench(self.root, [], discover=True)
        wb.record(wb.data()['jobs'][0]['key'], 'applied', '')
        with open(self.csv, encoding='utf-8-sig') as f:
            self.assertEqual([r['Company'] for r in csv.DictReader(f)], ['Acme'])

    def test_bridge_keeps_any_ats_application_url(self):
        row = dict(sourceUrl='https://www.linkedin.com/jobs/view/123/', status='fetched', title='Engineer', company='H&R Block',
                   location='KC', text='x' * 300, application_url='https://careers-hrblock.icims.com/jobs/1234/ml-engineer/job')
        self.assertIn('application_url', validate_records([row], 5)[0])
        row['application_url'] = 'https://evil.example.com/jobs/1234'
        self.assertNotIn('application_url', validate_records([row], 5)[0])


if __name__ == '__main__':
    unittest.main()
