"""Roles analysed in an earlier batch are not sent to the LLM again (2026-11-12)."""
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from job_identity import analyzed_index  # noqa: E402
from prepare_batch import select_candidates  # noqa: E402
from search_rotation import linkedin_search_url  # noqa: E402

CONFIG = dict(include_titles=['engineer', 'analyst'], exclude_titles=['senior'], allow_unknown_location=True,
              locations=['TX'], location_scope='us', preferred_locations=[], max_candidates=12,
              max_per_company=3, max_per_source=12)


def rec(i, company, title, **kw):
    return dict(sourceUrl=f'https://www.linkedin.com/jobs/view/{i}/', title=title, company=company,
                location='Dallas, TX', status='fetched', text='x' * 300, **kw)


class AnalyzedIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        today = date.today()
        old = (today - timedelta(days=90)).strftime('%Y%m%d')
        new = (today - timedelta(days=3)).strftime('%Y%m%d')
        a = dict(fit_score=80, worth_applying=True)
        batches = {
            f'batch-{new}': [dict(rec(1, 'Torentify', 'Data Analyst - SQL & Dashboards'), status='ready_for_review', analysis=a),
                             dict(rec(2, 'WebFX', 'Jr. Business Data Analyst'), status='not_recommended', analysis=a),
                             dict(rec(3, 'Broken Co', 'Software Engineer'), status='analysis_failed', analysis=a),
                             dict(rec(4, 'Affirm', 'Software Engineer I'), status='ready_for_review', analysis=a,
                                  application_url='https://job-boards.greenhouse.io/affirm/jobs/8017340003')],
            f'batch-{old}': [dict(rec(5, 'Old Co', 'Systems Analyst'), status='not_recommended', analysis=a)],
            'current-run': [dict(rec(6, 'Self Co', 'IT Analyst'), status='ready_for_review', analysis=a)],
        }
        for folder, rows in batches.items():
            (self.root / 'tmp' / folder).mkdir(parents=True)
            (self.root / 'tmp' / folder / 'queue.json').write_text(json.dumps(rows), encoding='utf-8')
        self.families, self.officials = analyzed_index(self.root, self.root / 'tmp' / 'current-run', 60)

    def tearDown(self):
        self.tmp.cleanup()

    def test_index_contents(self):
        keys = set(self.families)
        self.assertIn('torentify:data analyst sql and dashboards', keys)
        self.assertIn('webfx:jr business data analyst', keys)          # not_recommended still counts
        self.assertNotIn('brokenco:software engineer', keys)            # failed analysis can be retried
        self.assertNotIn('oldco:systems analyst', keys)                 # outside the window
        self.assertNotIn('selfco:it analyst', keys)                     # the run being prepared
        self.assertIn('greenhouse:8017340003', self.officials)

    def test_repost_and_same_official_job_skipped_before_analysis(self):
        records = [rec(11, 'Torentify', 'Data Analyst - SQL & Dashboards (Remote)'),
                   rec(12, 'Affirm Inc.', 'Full Stack Engineer, Search',
                       application_url='https://job-boards.greenhouse.io/affirm/jobs/8017340003?gh_src=x'),
                   rec(13, 'Torentify', 'Data Engineer')]
        selected, rejected = select_candidates(records, CONFIG, (), {}, {}, (self.families, self.officials))
        self.assertEqual([r['title'] for r in selected], ['Data Engineer'])
        self.assertEqual(sorted(r['reason'].split(':')[0] for r in rejected), ['already_analyzed', 'already_analyzed'])


class SearchUrlTests(unittest.TestCase):
    def test_linkedin_entry_level_only(self):
        url = linkedin_search_url('data analyst', 'United States', 14, ['1', '2', '3'])
        self.assertIn('f_E=2&', url)


if __name__ == '__main__':
    unittest.main()
