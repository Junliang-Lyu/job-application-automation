"""Resume version selection, uniform upload filename, STEM OPT check (2026-11-02)."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from build_apply_queue import pick_resume  # noqa: E402
from workbench_materials import bundle, upload_name  # noqa: E402
from prepare_batch import prepare_candidates  # noqa: E402
import analyze_jds  # noqa: E402

# The candidate's own resume PDFs and profile summary are private (not in the public repository).
HAS_RESUMES = (ROOT / 'resume/versions/Junliang_Lyu_Resume_SWE.pdf').is_file()
HAS_PROFILE = (ROOT / 'config/candidate-summary.local.md').is_file()
needs_resumes = unittest.skipUnless(HAS_RESUMES, 'private resume files not present')


@needs_resumes
class PickResumeTests(unittest.TestCase):
    def test_each_track_maps_to_its_version(self):
        for track, name in [('swe', 'SWE'), ('data', 'Data'), ('it_analyst', 'IT_Analyst'), ('ai', 'AI')]:
            self.assertEqual(pick_resume({'resume_track': track}), f'resume/versions/Junliang_Lyu_Resume_{name}.pdf')
            self.assertTrue((ROOT / pick_resume({'resume_track': track})).is_file())

    def test_unknown_or_missing_track_falls_back_to_swe(self):
        self.assertEqual(pick_resume({}), 'resume/versions/Junliang_Lyu_Resume_SWE.pdf')
        self.assertEqual(pick_resume({'resume_track': 'marketing'}), 'resume/versions/Junliang_Lyu_Resume_SWE.pdf')


class UploadNameTests(unittest.TestCase):
    def test_resume_is_copied_under_a_neutral_name(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / 'resume/versions/Junliang_Lyu_Resume_IT_Analyst.pdf'
            src.parent.mkdir(parents=True)
            src.write_bytes(b'%PDF-1.4 it')
            out = bundle(root, {'job_key': 'linkedin:1', 'company': 'Acme', 'title': 'IT Analyst'}, src, None)
            self.assertEqual(Path(out['files']['resume']).name, 'Junliang_Lyu_Resume.pdf')
            self.assertEqual(Path(out['files']['resume']).read_bytes(), b'%PDF-1.4 it')
        self.assertEqual(upload_name('cover_letter', Path('Cover_Letter.pdf')), 'Cover_Letter.pdf')


class StemTests(unittest.TestCase):
    def run_one(self, analysis):
        rec = dict(sourceUrl='https://www.linkedin.com/jobs/view/1/', title='Analyst', company='Acme', text='x' * 300)
        return prepare_candidates([rec], lambda r: analysis, lambda r, a: 'letter.pdf')[0]

    def base(self, **kw):
        return dict(fit_score=82, worth_applying=True, unknown_requirements=[], confirmed_conflicts=[],
                    cover_letter_body='Body.', fit_reason='ok', resume_track='data', **kw)

    def test_non_stem_role_is_not_recommended(self):
        e = self.run_one(self.base(stem_related=False))
        self.assertEqual(e['status'], 'not_recommended')
        self.assertIn('STEM', e['analysis']['confirmed_conflicts'][0])

    @needs_resumes
    def test_stem_role_uses_its_track_resume(self):
        e = self.run_one(self.base(stem_related=True))
        self.assertEqual(e['status'], 'ready_for_review')
        self.assertTrue(e['resume'].endswith('Junliang_Lyu_Resume_Data.pdf'))

    def test_prompt_asks_for_track_and_stem(self):
        self.assertIn('"resume_track"', analyze_jds.USER_TEMPLATE)
        self.assertIn('"stem_related"', analyze_jds.USER_TEMPLATE)
        self.assertNotIn('"objective"', analyze_jds.USER_TEMPLATE)
        if HAS_PROFILE:
            self.assertIn('Investment Research Lab', analyze_jds.CANDIDATE_SUMMARY)
        self.assertNotIn('Java OOP', analyze_jds.CANDIDATE_SUMMARY)


if __name__ == '__main__':
    unittest.main()
