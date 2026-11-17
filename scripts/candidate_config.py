"""Candidate-specific facts live in private, git-ignored files; the repository ships examples.

  config/candidate.local.json        name, contact line, best GPA, earliest start   (example: candidate.example.json)
  config/candidate-summary.local.md  profile text sent to the analysis model         (example: candidate-summary.example.md)
"""
import json
from functools import lru_cache
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / 'config'


def _pick(local, example):
    path = CONFIG / local
    return path if path.exists() else CONFIG / example


@lru_cache(maxsize=1)
def candidate():
    try:
        return json.loads(_pick('candidate.local.json', 'candidate.example.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def candidate_summary():
    try:
        return _pick('candidate-summary.local.md', 'candidate-summary.example.md').read_text(encoding='utf-8').strip()
    except OSError:
        return ''
