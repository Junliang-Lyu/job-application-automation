# Job Application Automation

A local, human-in-the-loop pipeline that cut the time I spent on job applications: it finds postings,
removes duplicates and reposts, scores fit with an LLM, prepares tailored materials, hands batches of
forms to a browser agent, and reconciles recruiting emails — while a person always presses **Submit**.

> Code only. The candidate profile, answer bank, resumes, application history and API keys are private
> and are not part of this repository; `config/*.example.*` shows their shape.

## What it does

| Stage | Module | Notes |
|---|---|---|
| Discover | `browser-extension/jobsearch-bridge`, `scripts/jobsearch_bridge.py` | An MV3 extension reads LinkedIn / Handshake search pages in the user's signed-in browser (read-only) and hands job descriptions to a localhost workbench through an authenticated bridge. Search keywords rotate (`config/search-queries.json`). |
| Deduplicate | `scripts/job_identity.py`, `scripts/prepare_batch.py` | Same job ID, same employer posting ID (Greenhouse, Lever, Ashby, Workday, iCIMS, …), normalized company + title "repost families", and roles already analysed in earlier batches are dropped **before** any paid LLM call. |
| Analyse | `scripts/analyze_jds.py` | DeepSeek (OpenAI-compatible JSON mode) returns a fit score, which of four resume versions to use, a cover-letter body, and hard facts (minimum GPA, start date, contract-only, staffing agency). Transient network errors are retried. |
| Screen | `scripts/qualification.py` | Code, not the model, applies the rules: only explicit hard limits (citizenship, clearance, required certifications, GPA above the candidate's, start date before availability, C2C-only) stop a job; everything else becomes a note. Staffing/aggregator posts are ranked last and expire after two days. |
| Prepare | `scripts/gen_cover_letter.py`, `scripts/workbench_materials.py` | Job-specific PDF cover letters; resume and letter copied to a short ASCII path for upload. |
| Review | `scripts/manual_workbench.py` + `.html` | A localhost desk (token-protected, Origin-checked) listing ready jobs, notes, materials and answer cards. |
| Fill | `scripts/codex_handoff.py`, `scripts/codex_rounds.py`, `scripts/codex_round.py` | Writes a task file for a browser agent (OpenAI Codex): fill each form from the profile and answer bank and **stop before Submit**. The agent asks the workbench, through request files, to check that jobs are still open and — when a round is done — to search again and write the next round. It ends with one numbered checklist; the person replies "submit 1, 3". |
| Track | `scripts/mark.py`, `scripts/gmail_reconcile.py` | One command records an outcome. Read-only Gmail OAuth (PKCE) matches recruiting emails to applications and classifies confirmations, rejections, assessments and interviews into a reply-rate report. |

## Design rules

- **A person submits.** Scripts never submit applications; the agent submits only items the person names, clicks once, and never retries an unclear result.
- **No credentials in the loop.** Passwords, MFA, CAPTCHA, account creation and consent checkboxes are left to the person.
- **Facts come from files the person wrote.** The agent may not invent experience, dates or numbers; missing answers are collected and asked once at the end.
- **Spend LLM calls last.** Everything that can be decided by IDs and rules runs before the model.
- **Web content is data, not instructions.**

## Layout

```
browser-extension/jobsearch-bridge/   MV3 extension: search, read JD, open-check (read-only)
scripts/                              workbench, pipeline, agent hand-off, Gmail reconcile
config/                               search keywords, title filters, ATS boards, agency names, examples
tests/                                unittest + node tests (130+)
```

## Running it

1. Python 3.11+, Node 18+ (tests only). `pip install -r requirements.txt` (Playwright is optional, used by a few tests and the assisted-fill helper).
2. Copy `config/candidate.example.json` → `config/candidate.local.json` and `config/candidate-summary.example.md` → `config/candidate-summary.local.md`; write `profile.md` and `job-application-answer-bank.md` in the project root (see `profile.example.md`).
3. Put `DEEPSEEK_API_KEY=...` in `.env`.
4. Load `browser-extension/jobsearch-bridge` as an unpacked extension in the browser profile you search with.
5. `python scripts/launch_workbench.py` → http://127.0.0.1:8765

```
python -m unittest discover -s tests -p "test_*.py"
for f in tests/*.test.cjs; do node "$f"; done
```

The workbench UI and agent task text are in Chinese (the author's working language).

## License

MIT
