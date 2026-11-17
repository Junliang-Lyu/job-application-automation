import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import public_ats


class PlatformEntryTests(unittest.TestCase):
    def test_default_requests_platform_discovery_without_fetching_fixed_boards(self):
        with tempfile.TemporaryDirectory() as d:
            result = subprocess.run([sys.executable, str(ROOT/'scripts/prepare_batch.py'),
                                     '--run-dir', d, '--discover-only'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            summary = json.loads((Path(d)/'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['status'], 'needs_platform_discovery')
            plan = json.loads((Path(d)/'browser-search-plan.json').read_text(encoding='utf-8'))
            self.assertEqual({r['source'] for r in plan}, {'LinkedIn', 'Handshake'})
            self.assertFalse((Path(d)/'fetched.json').exists())

    def test_full_jd_retains_eligibility_at_end(self):
        text = 'Build software. ' * 800 + 'No current or future sponsorship.'
        row = public_ats.record('https://example.test/job', 'Software Engineer', text, 'Greenhouse')
        self.assertTrue(row['text'].endswith('No current or future sponsorship.'))


if __name__ == '__main__':
    unittest.main()
