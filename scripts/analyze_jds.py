"""
JD Batch Analyzer — calls the configured LLM (DeepSeek in practice; see .env) to assess fit and draft a cover letter body.

Usage:
  python scripts/analyze_jds.py --input tmp/public-jds.json --output tmp/analyzed-jds.json

Input:  JSON output from fetch-public-jds.ps1  (list of {title, text, sourceUrl, status})
Output: Same list with an added "analysis" field on each record.

Requires:
  pip install anthropic
  Set ANTHROPIC_API_KEY environment variable (or put it in a .env file, never commit it).
"""

import argparse
import json
import os
import sys
import time
from urllib.request import Request, urlopen
from datetime import date
from pathlib import Path
from job_identity import prepare_records
from build_apply_queue import load_submitted_urls, load_excluded_urls

from runtime_config import get_analysis_llm_config
from candidate_config import candidate, candidate_summary

try:
    import anthropic
except ImportError:
    anthropic = None

# ── Candidate summary sent to the API: config/candidate-summary.local.md (private), else the example file ──
CANDIDATE_SUMMARY = candidate_summary()

AVAILABLE_FROM = candidate().get('available_from', 'the candidate\'s availability date')

SYSTEM_PROMPT = f"""You are a job-application fit assessor and cover-letter drafter.
Candidate profile:
{CANDIDATE_SUMMARY}

Rules:
- Reuse the graduation date and work-authorization facts stated in the profile above; do not request these facts again.
- Write fit_reason, unknown_requirements and confirmed_conflicts in concise Chinese; each item should describe one concrete condition. Do not ask again for facts the profile already confirms; ask only for specific evidence when required. Keep cover_letter_body in English.
- Never invent skills, technologies, metrics, or experience not listed above.
- cover_letter_body: 2–3 short paragraphs. No greeting, no sign-off. English only.
- Follow the Career Center structure in cover-letter/examples/Cover Letter Sample 2.pdf:
  Opening: explicitly name the role, explain specific interest, and introduce transferable skills.
  The Sale: connect one or two concrete experiences and supported outcomes to the JD;
  go beyond a skills list. Describe prior employment accurately as IT and network support.
  The Close: connect back to the role, invite an interview/discussion, and thank the reader.
  Never invent a referral, conversation, or how the candidate found the role.
- Missing profile evidence is unknown, not proof of absence. Return unknown_requirements for requirements needing confirmation; do not reject solely for them or invent fulfillment. Return confirmed_conflicts only for supported incompatibilities. Chinese fluent does not by itself establish Mandarin proficiency.
- If the JD clearly requires 3+ years of professional experience or skills entirely absent
  from the candidate profile, set worth_applying to false.
- Choose resume_track from the four resume versions above, then judge fit_score against THAT track's skills:
  an entry-level data analyst or IT analyst role is not penalized for asking little software engineering.
- STEM relevance: set stem_related to false only when the job's core duties are not computer science or data work
  (for example sales, recruiting, customer service, marketing, or general administration with incidental software use).
  When stem_related is false, set worth_applying to false and add a confirmed_conflicts item explaining why.
- Do not penalize the candidate for skills that the JD lists as preferred or nice-to-have.
- Hard facts for screening (report them; code applies the rules):
  min_gpa_required: the explicit minimum GPA the JD requires (number), else null; never infer one.
  starts_before_available: true only when the JD states the job or program starts before {AVAILABLE_FROM}
  (the earliest date the candidate can start work), or requires current enrollment after the candidate graduates.
  contract_only: true only when the role is explicitly C2C / corp-to-corp / 1099 / third-party vendor only, with no W2 employee option.
  staffing_agency: true when the poster is a staffing, recruiting or contracting firm hiring for an unnamed client,
  a talent pool / marketplace, or a "job placement" or training-then-placement program.
- Respond with valid JSON only. No markdown fences, no commentary."""

USER_TEMPLATE = """Analyze this job description for the candidate above.

Job title: {title}
Job description:
{text}

Return exactly this JSON structure:
{{
  "fit_score": <0-100 integer>,
  "fit_reason": "<one sentence>",
  "worth_applying": <true|false>,
  "unknown_requirements": ["<unverified requirement, or empty list>"],
  "confirmed_conflicts": ["<supported incompatibility, or empty list>"],
  "resume_track": "<swe|data|it_analyst|ai>",
  "stem_related": <true|false>,
  "min_gpa_required": <number or null>,
  "starts_before_available": <true|false>,
  "contract_only": <true|false>,
  "staffing_agency": <true|false>,
  "cover_letter_body": "<paragraph 1>\\n\\n<paragraph 2>\\n\\n<optional paragraph 3>"
}}"""


def response_text(content) -> str:
    for block in content:
        text = getattr(block, "text", None)
        if text:
            return text.strip()
    raise ValueError("Provider response did not contain a text block")


def deepseek_content(payload: dict) -> str:
    """Read the non-streaming OpenAI-compatible DeepSeek response body."""
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("DeepSeek response did not contain message content") from exc
    if not isinstance(content, str) or not content.strip():
        raise ValueError("DeepSeek response did not contain message content")
    return content.strip()


def parse_deepseek_analysis(raw: str) -> dict:
    """Parse DeepSeek JSON mode output, including its occasional content wrapper."""
    analysis = json.loads(raw)
    if analysis.get("type") == "json_object" and isinstance(analysis.get("content"), dict):
        return analysis["content"]
    return analysis


def load_completed_analyses(output_path: Path) -> dict[str, dict]:
    """Return analyses from a prior checkpoint, keyed by source URL."""
    if not output_path.exists():
        return {}
    try:
        with output_path.open(encoding="utf-8") as f:
            records = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(records, list):
        return {}
    return {
        record["sourceUrl"]: record["analysis"]
        for record in records
        if isinstance(record, dict)
        and record.get("sourceUrl")
        and isinstance(record.get("analysis"), dict)
        and "error" not in record["analysis"]
    }


def write_checkpoint(output_path: Path, results: list[dict]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def analyze_one(client: "anthropic.Anthropic", record: dict, model: str) -> dict:
    """Send one JD to the API and return the parsed analysis dict."""
    title = record.get("title") or "Unknown Role"
    text = (record.get("text") or "")[:6000]  # ~1500 tokens; keep cost low

    message = client.messages.create(
        model=model,
        max_tokens=900,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user",
                   "content": USER_TEMPLATE.format(title=title, text=text)}],
    )
    raw = response_text(message.content)
    return json.loads(raw)


def analyze_one_deepseek(api_key: str, base_url: str, record: dict, model: str) -> dict:
    """Use DeepSeek's native OpenAI-compatible JSON mode."""
    title = record.get("title") or "Unknown Role"
    text = (record.get("text") or "")[:6000]
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(title=title, text=text)},
        ],
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    request = Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    raw_payload = _call_with_retry(lambda: urlopen(request, timeout=60))
    return parse_deepseek_analysis(deepseek_content(raw_payload))


RETRY_WAITS = (5, 15, 30)  # seconds; DNS hiccups and 5xx/429 usually clear within a minute


def _transient(exc) -> bool:
    from urllib.error import HTTPError, URLError
    if isinstance(exc, HTTPError):
        return exc.code == 429 or exc.code >= 500
    return isinstance(exc, (URLError, TimeoutError, ConnectionError, OSError))


def _call_with_retry(open_response, waits=RETRY_WAITS, sleep=None):
    """Open the request, retrying transient network failures; other errors surface at once."""
    import time
    sleep = sleep or time.sleep
    for attempt in range(len(waits) + 1):
        try:
            with open_response() as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            if attempt == len(waits) or not _transient(exc):
                raise
            sleep(waits[attempt])


def main():
    parser = argparse.ArgumentParser(description="Analyze JDs via Claude API")
    parser.add_argument("--input", default="tmp/public-jds.json", help="Path to fetched JD JSON")
    parser.add_argument("--output", default="tmp/analyzed-jds.json", help="Output path for analyzed results")
    parser.add_argument("--min-score", type=int, default=0,
                        help="Only print JDs with fit_score >= this value in the summary")
    args = parser.parse_args()

    try:
        llm = get_analysis_llm_config()
    except ValueError as exc:
        sys.exit(str(exc))
    api_key = llm["api_key"]
    if not api_key:
        sys.exit("Set the selected provider's API key in the repository .env file or environment.")

    if not os.path.exists(args.input):
        sys.exit(f"Input file not found: {args.input}")

    with open(args.input, encoding="utf-8") as f:
        records = json.load(f)

    # Support both a plain list and a wrapper dict
    if isinstance(records, dict) and "records" in records:
        records = records["records"]

    records = prepare_records(records, load_submitted_urls() | load_excluded_urls())
    output_path = Path(args.output)
    completed = load_completed_analyses(output_path)
    processable = [r for r in records if r.get("status") in ("ok", "fetched", "success") and r.get("text")]
    skipped     = [r for r in records if r not in processable]
    pending = [r for r in processable if r.get("sourceUrl") not in completed]

    print(f"Found {len(processable)} processable JDs, {len(skipped)} skipped/errored.", flush=True)
    if len(pending) != len(processable):
        print(f"Resuming: {len(processable) - len(pending)} completed analyses reused.\n", flush=True)
    else:
        print(flush=True)

    client = None
    if llm["provider"] == "anthropic":
        if anthropic is None:
            sys.exit("Missing dependency: pip install anthropic")
        client = anthropic.Anthropic(api_key=api_key)
    results = [
        {**record, "analysis": completed[record["sourceUrl"]]}
        for record in processable
        if record.get("sourceUrl") in completed
    ]

    for i, record in enumerate(pending, 1):
        title = record.get("title") or record.get("sourceUrl") or f"JD #{i}"
        print(f"[{i}/{len(pending)}] Analyzing: {title[:70]}", flush=True)
        try:
            if llm["provider"] == "deepseek":
                analysis = analyze_one_deepseek(
                    api_key, llm["base_url"] or "https://api.deepseek.com", record, llm["model"]
                )
            else:
                analysis = analyze_one(client, record, llm["model"])
            score = analysis.get("fit_score", 0)
            worth = analysis.get("worth_applying", False)
            print(f"  → fit={score}  worth_applying={worth}", flush=True)
            results.append({**record, "analysis": analysis})
        except Exception as exc:
            print(f"  Failed: {exc}", flush=True)
            results.append({**record, "analysis": {"error": str(exc)}})

        write_checkpoint(output_path, results)

        if i < len(pending):
            time.sleep(0.4)   # gentle rate limiting

    # Append skipped records unchanged
    results.extend(skipped)
    write_checkpoint(output_path, results)

    # Summary
    analyzed = [r for r in results if "analysis" in r and "fit_score" in r["analysis"]]
    worth     = [r for r in analyzed if r["analysis"].get("worth_applying")]
    print(f"\n{'='*60}")
    print(f"Results saved to: {args.output}")
    print(f"Analyzed: {len(analyzed)}  |  Worth applying: {len(worth)}")
    if worth:
        print(f"\nTop candidates (score ≥ {args.min_score}):")
        top = sorted(worth, key=lambda r: r["analysis"]["fit_score"], reverse=True)
        for r in top:
            if r["analysis"]["fit_score"] >= args.min_score:
                print(f"  {r['analysis']['fit_score']:3d}  {r.get('title','?')[:60]}")
                print(f"       {r.get('sourceUrl','')[:70]}")


if __name__ == "__main__":
    main()
