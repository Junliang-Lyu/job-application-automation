"""
Parameterized Cover Letter Generator — replaces all individual build_*_cover_letter_pdf.py scripts.

Usage:
  python scripts/gen_cover_letter.py \
    --company "Acme Corp" \
    --role "Software Engineer" \
    --body-file tmp/acme-cover-body.txt \
    --date "September 17, 2026"

Or pipe body text directly:
  echo "Body text here..." | python scripts/gen_cover_letter.py --company "Acme" --role "SWE" --stdin

Reads body from:
  1. --body-file path (plain text or taken from analyze_jds.py output)
  2. --analyzed-json + --url  (reads cover_letter_body from that record automatically)
  3. --stdin

Output: cover-letter/generated/<file_prefix>_{Company}_{Role}_Cover_Letter.pdf
"""

import argparse
import json
import os
import re
import sys
from datetime import date

try:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
except ImportError:
    sys.exit("Missing: pip install reportlab")

# ── Contact header (public info only) ──────────────────────────────────────────
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parent))
from candidate_config import candidate  # noqa: E402

_C = candidate()
NAME = _C.get('name', 'Candidate Name')
PREFIX = _C.get('file_prefix', 'Candidate')
_contact = _C.get('contact', [])
HEADER_CONTACT = "&nbsp;&nbsp;|&nbsp;&nbsp;".join(_contact[:3]) + ("<br/>" + "&nbsp;&nbsp;|&nbsp;&nbsp;".join(_contact[3:]) if _contact[3:] else "")

# ── ReportLab styles ───────────────────────────────────────────────────────────
def _build_styles():
    base = getSampleStyleSheet()
    name_style = ParagraphStyle(
        "Name", parent=base["Normal"],
        fontName="Helvetica-Bold", fontSize=14, leading=17,
        alignment=TA_CENTER, spaceAfter=2,
    )
    contact_style = ParagraphStyle(
        "Contact", parent=base["Normal"],
        fontName="Helvetica", fontSize=9.2, leading=11,
        alignment=TA_CENTER, spaceAfter=14,
    )
    body_style = ParagraphStyle(
        "Body", parent=base["Normal"],
        fontName="Helvetica", fontSize=9.8, leading=13.5,
        alignment=TA_LEFT, spaceAfter=7,
    )
    return name_style, contact_style, body_style


def _slug(text: str, limit: int = 40) -> str:
    # Keep Windows paths well under the 260-character limit once copied into materials folders.
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:limit].rstrip("_")


def generate(company: str, role: str, body: str,
             letter_date: str | None = None,
             recipient: str = "Hiring Team",
             out_dir: str = "cover-letter/generated") -> str:
    """Build the PDF and return the output path."""

    today = date.today()
    letter_date = letter_date or f"{today:%B} {today.day}, {today.year}"
    os.makedirs(out_dir, exist_ok=True)

    filename = f"{PREFIX}_{_slug(company)}_{_slug(role)}_Cover_Letter.pdf"
    output_path = os.path.join(out_dir, filename)

    doc = SimpleDocTemplate(
        output_path, pagesize=LETTER,
        rightMargin=0.8 * inch, leftMargin=0.8 * inch,
        topMargin=0.62 * inch, bottomMargin=0.62 * inch,
    )

    name_s, contact_s, body_s = _build_styles()

    story = [
        Paragraph(NAME, name_s),
        Paragraph(HEADER_CONTACT, contact_s),
        Paragraph(letter_date, body_s),
        Paragraph(f"{recipient}<br/>{company}", body_s),
        Paragraph("Dear Hiring Team,", body_s),
    ]

    # Body paragraphs — split on blank lines
    for para in body.strip().split("\n\n"):
        para = para.strip().replace("\n", " ")
        if para:
            story.append(Paragraph(para, body_s))

    story += [
        Spacer(1, 4),
        Paragraph(f"Sincerely,<br/>{NAME}", body_s),
    ]

    doc.build(story)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Generate a cover letter PDF")
    parser.add_argument("--company",       required=True)
    parser.add_argument("--role",          required=True)
    parser.add_argument("--body-file",     help="Plain-text file with cover letter body")
    parser.add_argument("--analyzed-json", help="Output from analyze_jds.py")
    parser.add_argument("--url",           help="Source URL to match in analyzed-json")
    parser.add_argument("--stdin",         action="store_true", help="Read body from stdin")
    parser.add_argument("--date",          help="Override letter date (default: today)")
    parser.add_argument("--recipient",     default="Hiring Team")
    parser.add_argument("--out-dir",       default="cover-letter/generated")
    args = parser.parse_args()

    body = ""

    if args.analyzed_json and args.url:
        with open(args.analyzed_json, encoding="utf-8") as f:
            records = json.load(f)
        match = next((r for r in records
                      if r.get("sourceUrl") == args.url or r.get("normalizedUrl") == args.url), None)
        if not match:
            sys.exit(f"URL not found in {args.analyzed_json}: {args.url}")
        body = match["analysis"].get("cover_letter_body", "")
    elif args.body_file:
        with open(args.body_file, encoding="utf-8") as f:
            body = f.read()
    elif args.stdin:
        body = sys.stdin.read()

    if not body.strip():
        sys.exit("No body text provided. Use --body-file, --analyzed-json + --url, or --stdin.")

    out = generate(
        company=args.company,
        role=args.role,
        body=body,
        letter_date=args.date,
        recipient=args.recipient,
        out_dir=args.out_dir,
    )
    print(f"Generated: {out}")


if __name__ == "__main__":
    main()
