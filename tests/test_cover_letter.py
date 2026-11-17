import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


@unittest.skipUnless(importlib.util.find_spec('reportlab'), 'Use bundled PDF Python runtime')
class CoverLetterTests(unittest.TestCase):
    def test_default_date_generates_pdf_on_windows(self):
        from gen_cover_letter import generate
        with tempfile.TemporaryDirectory() as directory:
            result = generate('Example', 'Engineer', 'A factual application.', out_dir=directory)
            self.assertTrue(Path(result).read_bytes().startswith(b'%PDF-'))
