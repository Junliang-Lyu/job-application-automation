import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import public_ats


class PublicAtsTests(unittest.TestCase):
    def test_greenhouse_records_are_normalized_as_fetched_jobs(self):
        payload = {
            "jobs": [{
                "id": 123,
                "title": "Backend Engineer",
                "absolute_url": "https://boards.greenhouse.io/example/jobs/123",
                "content": "<p>Build APIs</p>",
                "location": {"name": "Dallas, TX"},
            }]
        }
        with patch.object(public_ats, "read_json", return_value=payload):
            records = public_ats.fetch_greenhouse_board("example")

        self.assertEqual(records[0]["_source"], "Greenhouse")
        self.assertEqual(records[0]["status"], "fetched")
        self.assertEqual(records[0]["title"], "Backend Engineer")
        self.assertIn("Build APIs", records[0]["text"])
        self.assertEqual(records[0]["location"], "Dallas, TX")
        self.assertEqual(records[0]["board"], "example")
        self.assertTrue(records[0]["contentHash"])

    def test_failed_board_is_needs_human_without_stopping_other_sources(self):
        with patch.object(public_ats, "read_json", side_effect=OSError("network unavailable")):
            records = public_ats.fetch_greenhouse_board("example")

        self.assertEqual(records[0]["status"], "needs_human")
        self.assertIn("network unavailable", records[0]["error"])


if __name__ == "__main__":
    unittest.main()
