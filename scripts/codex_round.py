"""Codex's command for continuous rounds (talks to the running workbench through files, not the network).

  python scripts/codex_round.py check <context.json>   # mark closed jobs before filling
  python scripts/codex_round.py next  <context.json>   # search if needed and write the next task file
  python scripts/codex_round.py wait  <request-id>     # keep waiting for a request that took long
"""
import json
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'tmp/codex-rounds'
MAX_WAIT = 540  # stay under typical shell-command time limits


def heartbeat_age():
    """Seconds since the workbench last wrote its heartbeat; None if unreadable. Retries a read that
    lands while the file is being rewritten."""
    beat = FOLDER / 'worker.alive'
    for _ in range(4):
        try:
            when = datetime.fromisoformat(beat.read_text(encoding='utf-8').strip())
            return (datetime.now().astimezone() - when).total_seconds()
        except (OSError, ValueError):
            time.sleep(1.5)
    return None


def alive():
    age = heartbeat_age()
    return age is not None and age < 60


def wait(rid, limit=MAX_WAIT):
    response = FOLDER / f'{rid}.response.json'
    start = time.monotonic()
    while time.monotonic() - start < limit:
        if response.exists():
            return json.loads(response.read_text(encoding='utf-8'))
        time.sleep(2)
    return None


def main(argv):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass
    if len(argv) != 2 or argv[0] not in ('check', 'next', 'wait'):
        print(__doc__)
        return 2
    if not alive():
        # Do not trust one stale reading: leave the request anyway and give the workbench a minute to answer.
        print(f'（提示：工作台心跳读取结果 {heartbeat_age()} 秒；仍然尝试发送请求）')
    if argv[0] == 'wait':
        rid = argv[1]
    else:
        context = Path(argv[1]).resolve()
        if not context.is_file():
            print(f'找不到岗位清单：{context}')
            return 2
        rid = datetime.now().strftime('%Y%m%d-%H%M%S-') + secrets.token_hex(3)
        FOLDER.mkdir(parents=True, exist_ok=True)
        (FOLDER / f'{rid}.request.json').write_text(json.dumps({'action': argv[0], 'context': str(context)}), encoding='utf-8')
    result = wait(rid, MAX_WAIT if alive() else 60)
    if result is None and not alive():
        print('工作台没有在运行：请让用户双击“求职工作台”打开，并保持 Job Search 里的工作台标签打开，然后再运行本命令。')
        print(f'（请求 {rid} 已留下，工作台启动后会处理；之后运行 python scripts/codex_round.py wait {rid}）')
        return 3
    if result is None:
        print(f'还在进行（搜岗和分析可能要 10 分钟）。请再运行：python scripts/codex_round.py wait {rid}')
        return 4
    print(result.get('message') or result.get('error') or '')
    if result.get('path'):
        print(f"新的任务文件：{result['path']}")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
