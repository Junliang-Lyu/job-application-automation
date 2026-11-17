"""Repost guard and queue freshness (2026-10-26)."""
import csv
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from job_identity import norm_title, repost_family, recent_applied_families  # noqa: E402
from manual_workbench import Workbench, FIELDS  # noqa: E402
from prepare_batch import select_candidates  # noqa: E402


def write_csv(path, rows):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({**dict.fromkeys(FIELDS, ''), **r})


class RepostFamilyTests(unittest.TestCase):
    def test_same_role_reposted_matches(self):
        self.assertEqual(repost_family('BeaconFire', 'Backend Software Engineer'),
                         repost_family('BeaconFire Inc.', 'Backend Software Engineer (Remote)'))

    def test_different_roles_same_company_do_not_match(self):
        self.assertNotEqual(repost_family('Atlassian', 'Software Engineer, 2027 Graduate U.S'),
                            repost_family('Atlassian', 'Machine Learning Engineer, 2027 Graduate U.S'))
        self.assertNotEqual(repost_family('Quora', 'Software Engineer New Grad, Machine Learning Platform'),
                            repost_family('Quora', 'Full Stack Software Engineer, New Grad - Quora (Remote)'))

    def test_trailing_company_suffix_removed(self):
        self.assertEqual(norm_title('Full Stack Engineer - Quora (Remote)', 'Quora'), 'full stack engineer')

    def test_window_limits_history(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.csv'
            old = (date.today() - timedelta(days=90)).isoformat()
            new = (date.today() - timedelta(days=5)).isoformat()
            write_csv(p, [dict(Company='Old Co', Position='Engineer', Status='Applied', **{'Date Applied': old}),
                          dict(Company='New Co', Position='Engineer', Status='Applied', **{'Date Applied': new}),
                          dict(Company='Maybe Co', Position='Engineer', Status='Submission Unknown', **{'Date Applied': new})])
            fam = recent_applied_families(p, 60)
            self.assertIn(repost_family('New Co', 'Engineer'), fam)
            self.assertNotIn(repost_family('Old Co', 'Engineer'), fam)
            self.assertNotIn(repost_family('Maybe Co', 'Engineer'), fam)


class PrepareBatchRepostTests(unittest.TestCase):
    def test_repost_screened_before_analysis(self):
        config = dict(include_titles=['engineer'], exclude_titles=[], allow_unknown_location=True,
                      locations=['TX'], location_scope='us', preferred_locations=['TX'],
                      max_candidates=10, max_per_company=3, max_per_source=10)
        rec = dict(sourceUrl='https://www.linkedin.com/jobs/view/999/', title='Backend Software Engineer',
                   company='BeaconFire', location='Dallas, TX', status='fetched', text='x' * 300)
        fams = {repost_family('BeaconFire', 'Backend Software Engineer'): ('2026-10-14', 'Backend Software Engineer', 'handshake:1')}
        selected, rejected = select_candidates([rec], config, (), fams)
        self.assertEqual(selected, [])
        self.assertEqual(rejected[0]['reason'], 'likely_repost_of_applied:2026-10-14')


class WorkbenchFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / 'applications').mkdir()
        (root / 'resume/main').mkdir(parents=True)
        (root / 'resume/main/Junliang_Lyu_Main_Resume_v5_1.pdf').write_bytes(b'%PDF-1.4')
        write_csv(root / 'applications/applications.csv', [dict(
            Company='Precisely', Position='Associate software Engineer (Python AI)', Status='Applied',
            **{'Date Applied': date.today().isoformat(), 'Job URL': 'https://www.linkedin.com/jobs/view/111/'})])
        old = (date.today() - timedelta(days=20)).strftime('%Y%m%d')
        new = date.today().strftime('%Y%m%d')
        a = dict(fit_score=80, worth_applying=True)
        self.queues = []
        for folder, rows in [(f'batch-{old}', [dict(sourceUrl='https://www.linkedin.com/jobs/view/201/', title='Old Role', company='OldCo', status='ready_for_review', analysis={**a, 'fit_score': 95})]),
                             (f'batch-{new}', [dict(sourceUrl='https://www.linkedin.com/jobs/view/202/', title='New Role', company='NewCo', status='ready_for_review', analysis=a),
                                               dict(sourceUrl='https://www.linkedin.com/jobs/view/222/', title='Associate software Engineer (Python AI)', company='Precisely', status='ready_for_review', analysis=a)])]:
            (root / 'tmp' / folder).mkdir(parents=True)
            q = root / 'tmp' / folder / 'queue.json'
            q.write_text(json.dumps(rows), encoding='utf-8')
            self.queues.append(q)
        self.wb = Workbench(root, self.queues)

    def tearDown(self):
        self.tmp.cleanup()

    def jobs(self):
        return {j['company']: j for j in self.wb.data()['jobs']}

    def test_fresh_before_higher_scored_stale(self):
        order = [j['company'] for j in self.wb.data()['jobs'] if j['ready']]
        self.assertEqual(order, ['NewCo', 'OldCo'])
        self.assertTrue(self.jobs()['OldCo']['stale'])

    def test_repost_flagged_and_override(self):
        j = self.jobs()['Precisely']
        self.assertEqual(j['status'], 'likely_duplicate')
        self.assertFalse(j['ready'])
        self.wb.record(j['key'], 'pending', '仍要投', override_duplicate=True)
        self.assertEqual(self.jobs()['Precisely']['status'], 'pending')

    def test_applied_row_records_resume_version(self):
        self.wb.record(self.jobs()['NewCo']['key'], 'applied', '')
        with open(self.wb.csv_path, encoding='utf-8-sig') as f:
            row = [r for r in csv.DictReader(f) if r['Company'] == 'NewCo'][0]
        self.assertEqual(row['Resume Version'], 'Junliang_Lyu_Main_Resume_v5_1.pdf')


if __name__ == '__main__':
    unittest.main()
