"""Split analysis "needs confirmation" items into real blockers vs. notes (candidate decision 2026-10-27).

The candidate applies broadly. Only a few categories can genuinely disqualify an application and
are held for the candidate's review; everything else (unstated sponsorship/OPT, start date,
graduation-window wording, location/travel, skills the resume doesn't show, preferred items)
is shown as a note and does not hold the job back.

Blockers:
  * citizenship / permanent residency / "U.S. person" / ITAR or export control / security clearance;
  * an explicit requirement to never need sponsorship ("now or in the future");
  * a required certification or license (e.g. Security+).
"""
import re

_BLOCKER = re.compile(
    r'citizen|公民|permanent resident|green card|绿卡|u\.?s\.? person|美国人身份|itar|export control|出口管制'
    r'|security clearance|clearance|安全许可|安全审查|public trust'
    r'|now or in the future|现在或将来|现在及将来|不需要.{0,6}(?:担保|赞助)|never (?:require|need) (?:visa )?sponsorship'
    r'|certification required|认证（岗位列为必需）|必需.{0,6}认证|认证.{0,6}(?:必需|必须|要求)|security\+|ciss?p|license required|执照', re.I)


# The JD is merely silent about it (and nothing says "only"/"required"): a note, not a blocker.
_SILENT = re.compile(r'未说明|未明确|未提及|没有说明|not stated|unstated|silent|does not (?:state|say|mention)|\bno (?:citizenship|clearance|permanent)', re.I)
_STRONG = re.compile(r'\bonly\b|仅|硬性|必需|必须|\brequired\b|clause|条款|itar|u\.?s\.? person', re.I)


def is_blocker(item):
    text = str(item)
    if not _BLOCKER.search(text):
        return False
    return not (_SILENT.search(text) and not _STRONG.search(text))


def split_requirements(items):
    """Return (blockers, notes)."""
    blockers, notes = [], []
    for item in items or []:
        (blockers if is_blocker(item) else notes).append(str(item))
    return blockers, notes


# Hard facts the analysis reports (analyze_jds fields); code, not the model, decides (2026-11-15).
from candidate_config import candidate
CANDIDATE_BEST_GPA = float(candidate().get('best_gpa') or 4.0)  # config/candidate.local.json
_AGENCY = re.compile(r"\bour client\b|on behalf of (?:our|a|an) client|staffing (?:agency|firm|company)|talent (?:pool|network|marketplace)"
                     r"|\bc2c\b|corp[- ]to[- ]corp|job placement program|placement program|bench sales", re.I)


def hard_conflicts(analysis):
    """Conflicts that make a job not worth sending to the candidate or Codex."""
    a = analysis or {}
    out = []
    gpa = a.get('min_gpa_required')
    if isinstance(gpa, (int, float)) and not isinstance(gpa, bool) and gpa > CANDIDATE_BEST_GPA:
        out.append(f'要求 GPA {gpa:g} 以上，高于你的 {CANDIDATE_BEST_GPA:g}')
    if a.get('starts_before_available') is True:
        out.append(f"开始时间早于你能入职的时间（{candidate().get('availability_note') or candidate().get('available_from', '')}）")
    if a.get('contract_only') is True:
        out.append('只接 C2C / 1099 / 外包合同，没有正式员工选项')
    return out


def agency_names():
    """Aggregators and staffing firms seen in the queue; edit config/agency-companies.json to add more."""
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'config/agency-companies.json'
    try:
        return [str(x).lower() for x in json.loads(path.read_text(encoding='utf-8'))]
    except (OSError, ValueError):
        return []


def looks_like_agency(analysis, text='', company=''):
    """Staffing / placement posts: shown last and dropped after two days (they disappear quickly)."""
    a = analysis or {}
    name = re.sub(r'[^a-z0-9]+', ' ', str(company).lower()).strip()
    if name and any(re.sub(r'[^a-z0-9]+', ' ', n).strip() == name for n in agency_names()):
        return True
    if isinstance(a.get('staffing_agency'), bool):
        return a['staffing_agency']
    return bool(_AGENCY.search(f'{company}\n{text or ""}'[:20000]))
