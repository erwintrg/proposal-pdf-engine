#!/usr/bin/env python3
"""Draft a proposal.json from discovery-call notes.

  python draft.py examples/acme-freight/notes.md --backend mock     # offline, replays recorded-draft.json
  python draft.py notes.md --backend anthropic                       # Claude via the API (ANTHROPIC_API_KEY)
  python draft.py notes.md --backend claude-cli                      # Claude via `claude -p` (Claude Code login)

The model writes the copy; prices, VAT, related systems and house style are checked in code, with
one repair round. Nothing is written unless every check passes.

Exit codes: 0 written, 1 the draft failed its checks, 2 the notes are missing information
(questions printed, nothing written), 3 backend error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from proposal_engine.drafting import DraftError, NeedsInput, draft, make_backend
from proposal_engine.pipeline import LayoutChecker, describe_totals
from proposal_engine.schema import ProposalError, dump_proposal
from proposal_engine.theme import load_theme


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("notes", type=Path, help="discovery-call notes (plain text or markdown)")
    ap.add_argument("--backend", required=True, choices=["mock", "anthropic", "claude-cli"])
    ap.add_argument("--recording", type=Path,
                    help="mock backend: recorded model answer (default: recorded-draft.json next to the notes)")
    ap.add_argument("--theme", default=os.environ.get("PROPOSAL_THEME", "ycat"),
                    help="theme name or folder; its agency block feeds the prompt and the VAT rules")
    ap.add_argument("--model", help="anthropic / claude-cli: model id (default: $PROPOSAL_MODEL, else claude-opus-5)")
    ap.add_argument("--date", type=dt.date.fromisoformat, help="proposal date, YYYY-MM-DD (default: today)")
    ap.add_argument("--sample", action="store_true", help="mark the cover as a sample with a fictional client")
    ap.add_argument("--max-repairs", type=int, default=1, help="repair rounds after a failed check (default: 1)")
    ap.add_argument("--no-layout-check", action="store_true",
                    help="skip laying the draft out in Chromium (overflowing pages then only show up in render.py)")
    ap.add_argument("--chromium", help="Chromium binary for the layout check (default: $PROPOSAL_CHROMIUM or bundled)")
    ap.add_argument("-o", "--out", type=Path, help="output path (default: out/<slug>/proposal.json)")
    args = ap.parse_args(argv)

    try:
        theme = load_theme(args.theme)
    except ProposalError as err:
        print("INVALID THEME\n" + "\n".join(f"  - {p}" for p in err.problems))
        return 1
    if not args.notes.is_file():
        print(f"file not found: {args.notes}")
        return 1
    notes = args.notes.read_text(encoding="utf-8")
    recording = args.recording or args.notes.with_name("recorded-draft.json")
    layout = None if args.no_layout_check else LayoutChecker(theme, chromium=args.chromium)

    try:
        backend = make_backend(args.backend, recording=recording, model=args.model)
        print(f"drafting with backend '{backend.name}' from {args.notes}")
        result = draft(notes, backend, theme, today=args.date or dt.date.today(), sample=args.sample,
                       max_repairs=args.max_repairs, layout_check=layout)
    except NeedsInput as err:
        print("NEEDS INPUT, nothing written. Answer these and run again:")
        for question in err.questions:
            print(f"  - {question}")
        return 2
    except DraftError as err:
        if err.problems:
            rejected = Path("out") / f"{args.notes.stem}.rejected.txt"
            rejected.parent.mkdir(parents=True, exist_ok=True)
            rejected.write_text(err.raw, encoding="utf-8")
            print(f"REJECTED: {err}\nlast answer saved to {rejected}")
            return 1
        print(f"BACKEND ERROR: {err}")
        return 3

    proposal = result.proposal
    out = args.out or Path("out") / proposal.effective_slug / "proposal.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dump_proposal(proposal), encoding="utf-8")
    repaired = f", repaired {len(result.repaired)} problem(s)" if result.repaired else ""
    checks = "schema, house style, prices from the notes, VAT rule, related systems"
    if layout and not layout.unavailable:
        checks += ", page layout"
    print(f"checks:  {checks}: ok (attempt {result.attempts}{repaired})")
    if layout and layout.unavailable:
        print(f"layout:  check skipped, no browser: {layout.unavailable.splitlines()[0]}")
    print(f"client:  {proposal.client.company}, {proposal.client.country}, {proposal.vat.label or 'no VAT label'}")
    print(f"totals:  {describe_totals(proposal)}")
    print(f"wrote:   {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
