"""Record an application result from the command line (same rules as the workbench).

Examples:
  python scripts/mark.py Affirm applied
  python scripts/mark.py "Quora full stack" unknown --note "点了提交，没看到提示"
  python scripts/mark.py BeaconFire skipped --pick 2
  python scripts/mark.py --list            # open items: to apply, unsure, likely duplicate
  python scripts/mark.py --key linkedin:4479198572 applied   # exact job (used by Codex)

Matching is case-insensitive on "company + title"; every word must appear.
If several jobs match, they are listed and nothing is written until --pick N is given.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manual_workbench import Workbench, ROOT  # noqa: E402

STATUS = {'applied': 'applied', 'unknown': 'submission_unknown', 'skipped': 'skipped', 'skip': 'skipped'}
OPEN = ('pending', 'needs_qualification_review', 'submission_unknown', 'likely_duplicate')
NAMES = {'applied': '已投', 'submission_unknown': '不确定', 'skipped': '跳过', 'pending': '待投',
         'needs_qualification_review': '资格待核对', 'likely_duplicate': '疑似重复', 'not_recommended': '不推荐'}


def line(i, j):
    return f"  [{i}] {j['company']} · {j['title']} · {j['location']} — {NAMES.get(j['status'], j['status'])}"


def main(argv=None, root=ROOT):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('query', nargs='?', help='公司或岗位关键词')
    p.add_argument('result', nargs='?', help='applied / unknown / skipped')
    p.add_argument('--key', help='精确的岗位编号（job_key），例如 linkedin:4479198572')
    p.add_argument('--note', default='', help='备注（可选）')
    p.add_argument('--pick', type=int, help='多个匹配时选择第几个')
    p.add_argument('--list', action='store_true', help='列出未完成的岗位')
    a = p.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')  # Windows console
    wb = Workbench(root, [], discover=True)
    jobs = wb.data()['jobs']
    if a.list:
        items = [j for j in jobs if j['status'] in OPEN]
        print(f'未完成 {len(items)} 个：')
        for i, j in enumerate(items, 1):
            print(line(i, j))
        return 0
    if a.key:
        a.result = a.result or a.query
        matches = [j for j in jobs if j['key'] == a.key]
        if not matches:
            print(f'没有编号为 {a.key} 的岗位。')
            return 1
    else:
        if not a.query or not a.result:
            p.error('需要关键词和结果，例如：python scripts/mark.py Affirm applied')
        words = a.query.lower().split()
        matches = [j for j in jobs if all(w in f"{j['company']} {j['title']}".lower() for w in words)]
    if a.result not in STATUS:
        p.error('结果只能是 applied / unknown / skipped')
    # Prefer jobs that still need a result; fall back to all matches.
    open_matches = [j for j in matches if j['status'] != 'applied']
    matches = open_matches or matches
    if not matches:
        print(f'没有找到包含「{a.query}」的岗位。用 --list 查看未完成岗位。')
        return 1
    if len(matches) > 1 and not a.pick:
        print(f'「{a.query}」匹配到 {len(matches)} 个岗位，没有写入。请加 --pick N 选择：')
        for i, j in enumerate(matches, 1):
            print(line(i, j))
        return 2
    if a.pick and not 1 <= a.pick <= len(matches):
        print(f'--pick 超出范围（1–{len(matches)}）')
        return 2
    job = matches[(a.pick or 1) - 1]
    status = STATUS[a.result]
    if job['status'] == 'applied':
        print(f"已经是已投：{job['company']} · {job['title']}，不再重复记录。")
        return 0
    wb.record(job['key'], status, a.note or f'命令行记录：{NAMES[status]}')
    print(f"已记录：{job['company']} · {job['title']} → {NAMES[status]}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
