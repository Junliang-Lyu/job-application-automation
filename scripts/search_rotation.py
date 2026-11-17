"""Rotate each search track after actual attempts, without touching browsers."""
import json
from pathlib import Path
from urllib.parse import urlencode

CONFIG = Path(__file__).resolve().parents[1] / 'config' / 'search-queries.json'
STATE = Path(__file__).resolve().parents[1] / 'tmp' / 'search-rotation.json'

def linkedin_search_url(keyword, location, days, workplace_types, easy_apply=False):
    params={'keywords':keyword,'location':location,'f_E':'2','f_JT':'F','sortBy':'DD',
            'f_TPR':f'r{int(days)*86400}','f_WT':','.join(workplace_types)}
    if easy_apply: params['f_AL']='true'
    return 'https://www.linkedin.com/jobs/search/?'+urlencode(params)


def load_queries():
    queries = json.loads(CONFIG.read_text(encoding='utf-8'))['linkedin']
    if not queries or any(not isinstance(q, str) or not q.strip() for q in queries):
        raise ValueError('Search queries must be a nonempty list of strings')
    return list(dict.fromkeys(queries))


def read_state(path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


def ordered_queries(queries, path=STATE, track='easy'):
    start = read_state(path).get(track, 0) % len(queries)
    return queries[start:] + queries[:start]


def record_attempt(queries, path, track, query):
    state = read_state(path)
    state[track] = (queries.index(query) + 1) % len(queries)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2), encoding='utf-8')
    temporary.replace(path)
