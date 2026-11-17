"""Command-line result recording (scripts/mark.py)."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import mark  # noqa: E402


class MarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'applications').mkdir()
        (self.root / 'tmp/batch-20261026').mkdir(parents=True)
        rows = [dict(sourceUrl=f'https://www.linkedin.com/jobs/view/{i}/', title=t, company=c, location='TX',
                     status='ready_for_review', analysis=dict(fit_score=80, worth_applying=True))
                for i, (c, t) in enumerate([('Affirm', 'Software Engineer I'), ('Quora', 'Full Stack Engineer'),
                                            ('Quora', 'ML Platform Engineer')], 1)]
        (self.root / 'tmp/batch-20261026/queue.json').write_text(json.dumps(rows), encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def run_mark(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = mark.main(list(args), root=self.root)
        return code, out.getvalue()

    def journal(self):
        p = self.root / 'applications/manual-workbench.local.jsonl'
        return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []

    def test_unique_match_records(self):
        code, _ = self.run_mark('affirm', 'applied')
        self.assertEqual(code, 0)
        self.assertEqual(self.journal()[-1]['status'], 'applied')
        self.assertTrue((self.root / 'applications/applications.csv').exists())

    def test_ambiguous_match_writes_nothing(self):
        code, out = self.run_mark('quora', 'unknown')
        self.assertEqual(code, 2)
        self.assertIn('--pick', out)
        self.assertEqual(self.journal(), [])

    def test_exact_key(self):
        self.assertEqual(self.run_mark('--key', 'linkedin:3', 'applied')[0], 0)
        self.assertEqual(self.journal()[-1]['job_key'], 'linkedin:3')
        self.assertEqual(self.run_mark('--key', 'linkedin:999', 'applied')[0], 1)
        with self.assertRaises(SystemExit):
            self.run_mark('--key', 'linkedin:2', 'maybe')

    def test_pick_and_multiword(self):
        self.assertEqual(self.run_mark('quora', 'skipped', '--pick', '2')[0], 0)
        self.assertEqual(self.run_mark('quora full', 'unknown')[0], 0)
        self.assertEqual([e['status'] for e in self.journal()], ['skipped', 'submission_unknown'])


if __name__ == '__main__':
    unittest.main()
