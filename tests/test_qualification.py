"""Only explicit hard limits hold a job for review (candidate decision 2026-10-27)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from qualification import split_requirements, is_blocker  # noqa: E402
from prepare_batch import prepare_candidates  # noqa: E402
from manual_workbench import Workbench  # noqa: E402

SOFT = ['JD未说明是否接受F-1 OPT就业', 'JD未说明是否支持未来H1B赞助', '是否了解GDP、数据完整性或CSV概念（JD加分，候选人未提供）',
        'JD未说明入职时间能否配合2026年12月毕业', 'Kubernetes 容器编排经验未确认', 'Linux fundamentals: not listed in the candidate profile',
        '岗位标注 College Grad 2027，候选人预计 2026 年 12 月毕业，是否符合该届招聘窗口需确认', '能否接受每周 3 天在 Redwood City 办公室现场办公需确认',
        'JD未说明是否要求候选人未来永不需要雇主担保（候选人毕业后需要H1B或其他担保）',
        'Whether Optum accepts F-1 OPT candidates (no citizenship or permanent-residency requirement is stated)']
HARD = ['是否具备通过 CBP 背景调查所需的身份与背景条件（JD 硬性要求，仅美国公民可满足）', '是否已持有 Security+ 认证（岗位列为必需）',
        "Which statement governs eligibility: the ITAR U.S.-person clause or the at-a-glance line",
        "Whether the 'U.S. Citizens Only' restriction is firm"]


class SplitTests(unittest.TestCase):
    def test_soft_items_are_notes(self):
        for x in SOFT:
            self.assertFalse(is_blocker(x), x)

    def test_hard_limits_are_blockers(self):
        for x in HARD:
            self.assertTrue(is_blocker(x), x)

    def test_split(self):
        self.assertEqual(split_requirements(SOFT[:2] + HARD[:1]), (HARD[:1], SOFT[:2]))


def analysis(unknown, worth=True, score=84):
    return dict(fit_score=score, worth_applying=worth, unknown_requirements=unknown, confirmed_conflicts=[],
                cover_letter_body='Body.', fit_reason='ok')


class PrepareTests(unittest.TestCase):
    def run_one(self, unknown, worth=True):
        rec = dict(sourceUrl='https://www.linkedin.com/jobs/view/1/', title='Software Engineer', company='Acme', text='x' * 300)
        return prepare_candidates([rec], lambda r: analysis(unknown, worth), lambda r, a: 'letter.pdf')[0]

    def test_unstated_items_go_straight_to_ready_with_hints(self):
        e = self.run_one(SOFT[:3])
        self.assertEqual(e['status'], 'ready_for_review')
        self.assertEqual(e['hints'], SOFT[:3])
        self.assertNotIn('reason', e)

    def test_hard_limit_still_held_for_review(self):
        e = self.run_one(SOFT[:1] + HARD[:1])
        self.assertEqual(e['status'], 'needs_qualification_review')
        self.assertEqual(e['reason'], '需要核对：' + HARD[0])


class WorkbenchTests(unittest.TestCase):
    def test_previously_held_jobs_are_released_unless_hard_limit(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'applications').mkdir()
            (root / 'resume/main').mkdir(parents=True)
            (root / 'resume/main/Junliang_Lyu_Main_Resume_v5_1.pdf').write_bytes(b'%PDF-1.4')
            (root / 'tmp/batch-20261027').mkdir(parents=True)
            rows = [dict(sourceUrl='https://www.linkedin.com/jobs/view/1/', title='Junior Software Developer', company='Soft Co',
                         status='needs_qualification_review', reason='需要核对：' + '；'.join(SOFT[:2]), analysis=analysis(SOFT[:2], worth=False)),
                    dict(sourceUrl='https://www.linkedin.com/jobs/view/2/', title='Software Engineer', company='Federal Co',
                         status='needs_qualification_review', analysis=analysis(SOFT[:1] + HARD[:1], worth=False))]
            (root / 'tmp/batch-20261027/queue.json').write_text(json.dumps(rows), encoding='utf-8')
            jobs = {j['company']: j for j in Workbench(root, [], discover=True).data()['jobs']}
            self.assertEqual(jobs['Soft Co']['status'], 'pending')
            self.assertTrue(jobs['Soft Co']['ready'])
            self.assertEqual(jobs['Soft Co']['note'], '')
            self.assertEqual(jobs['Soft Co']['hints'], SOFT[:2])
            self.assertEqual(jobs['Federal Co']['status'], 'needs_qualification_review')
            self.assertEqual(jobs['Federal Co']['note'], '需要核对：' + HARD[0])


if __name__ == '__main__':
    unittest.main()
