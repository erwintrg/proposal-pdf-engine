#!/usr/bin/env python3
"""Offline demo: fictional call notes -> proposal.json -> branded A4 PDF. No account, no API key.

  python demo.py

  1. draft     examples/acme-freight/notes.md -> out/acme-freight/proposal.json with the mock drafter
               (it replays a recorded model answer, then runs every check a live draft gets)
  2. render    out/sample-proposal.pdf in the YCAT theme, every page checked for overflow,
               plus PNG previews of pages 1 and 2 in docs/ for the README
  3. theme     the same proposal in the neutral theme: out/sample-proposal-neutral.pdf
  4. overflow  a copy with an overlong scope section, which the checker has to catch
  5. stripe    the deposit-link request that real mode would send (printed, nothing is sent)
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from proposal_engine.drafting import MockBackend, draft
from proposal_engine.integrations.stripe_deposit import deposit_link_request
from proposal_engine.money import totals_for
from proposal_engine.pdf import RenderError
from proposal_engine.pipeline import LayoutChecker, describe_totals, render_document, report_lines
from proposal_engine.schema import dump_proposal, parse_proposal
from proposal_engine.theme import load_theme

ROOT = Path(__file__).resolve().parent
EXAMPLE = ROOT / "examples" / "acme-freight"
OUT = ROOT / "out"
DOCS = ROOT / "docs"
SAMPLE_DATE = dt.date(2026, 10, 1)  # fixed, so the sample PDF and the previews do not change daily


def step(title: str) -> None:
    print(f"\n== {title}")


def rel(path: Path) -> str:
    return str(Path(path).resolve().relative_to(ROOT))


def main() -> int:
    load_dotenv()
    ok = True
    theme = load_theme("ycat")

    step("1/5 draft: call notes -> proposal.json (mock drafter, offline)")
    layout = LayoutChecker(theme)
    result = draft(
        (EXAMPLE / "notes.md").read_text(encoding="utf-8"),
        MockBackend(EXAMPLE / "recorded-draft.json"),
        theme,
        today=SAMPLE_DATE,
        sample=True,
        layout_check=layout,
    )
    proposal = result.proposal
    drafted = OUT / "acme-freight" / "proposal.json"
    drafted.parent.mkdir(parents=True, exist_ok=True)
    drafted.write_text(dump_proposal(proposal), encoding="utf-8")
    golden = json.loads((EXAMPLE / "proposal.json").read_text(encoding="utf-8"))
    same = json.loads(dump_proposal(proposal)) == golden
    print(f"   client:  {proposal.client.first_name} {proposal.client.last_name}, {proposal.client.company} ({proposal.client.country})")
    print(f"   checks:  schema, house style, prices from the notes, VAT rule, related systems"
          f"{', page layout' if not layout.unavailable else ''}: ok")
    print(f"   VAT:     {proposal.vat.label} (from the rule table, not from the model)")
    print(f"   totals:  {describe_totals(proposal)}")
    print(f"   wrote:   {rel(drafted)} (same as examples/acme-freight/proposal.json: {'yes' if same else 'NO'})")
    ok &= same

    step("2/5 render: proposal.json -> PDF (YCAT theme) + README previews")
    try:
        _, report = render_document(
            proposal, theme, OUT / "sample-proposal.pdf",
            previews=[(1, DOCS / "preview-page-1.png"), (2, DOCS / "preview-page-2.png")],
        )
    except RenderError as err:
        print(f"   RENDER FAILED: {err}")
        return 1
    for line in report_lines(report):
        print(f"   {line}")
    print(f"   pdf:     {rel(report.pdf_path)}")
    for preview in report.previews:
        print(f"   preview: {rel(preview)}")
    ok &= not report.problems

    step("3/5 theme: the same proposal in the neutral theme")
    _, neutral = render_document(
        proposal, load_theme("neutral"), OUT / "sample-proposal-neutral.pdf",
        previews=[(1, DOCS / "preview-neutral-page-1.png")],
    )
    print(f"   {report_lines(neutral)[1].strip()}")
    print(f"   pdf:     {rel(neutral.pdf_path)}")
    print(f"   preview: {rel(neutral.previews[0])}")
    ok &= not neutral.problems

    step("4/5 overflow check: 14 scope items of two lines each (expected to fail)")
    data = json.loads(dump_proposal(proposal))
    data["scope"]["items"] = [
        f"Deliverable {n}: one more workflow with its own error handling, tests, documentation and a recorded "
        "walkthrough, so the list keeps growing" for n in range(1, 15)
    ]
    _, long_report = render_document(parse_proposal(data), theme, OUT / "overflow-check.pdf")
    for problem in long_report.problems:
        print(f"   caught:  {problem}")
    caught = any(m.label == "Scope and timeline" for m in long_report.overflows)
    print(f"   result:  {'the overlong section was reported' if caught else 'NOT REPORTED'}")
    ok &= caught

    step("5/5 stripe: the deposit-link request real mode would send (nothing is sent)")
    request = deposit_link_request(proposal, totals_for(proposal), theme.agency)
    print(f"   product:  {request['product']['name']}")
    print(f"   price:    {request['price']['unit_amount']} {request['price']['currency']} (minor units)")
    print(f"   retry-safe id: {request['idempotency_key']} (Stripe idempotency)")

    print("\nDemo " + ("finished: open out/sample-proposal.pdf" if ok else "FAILED, see above"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
