#!/usr/bin/env python3
"""Render a proposal.json into a branded multi-page A4 PDF.

  python render.py examples/acme-freight/proposal.json             # -> out/acme-freight.pdf
  python render.py proposal.json --theme neutral -o out/neutral.pdf
  python render.py proposal.json --check                           # validate and print totals only
  python render.py proposal.json --stripe-dry-run                  # show the Stripe request, send nothing
  python render.py proposal.json --stripe --drive --share          # real mode, needs .env

Exit codes: 0 ok, 1 invalid input, 2 a page overflows (PDF still written for inspection),
3 browser or render error, 4 an integration failed (PDF written).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from proposal_engine.money import totals_for
from proposal_engine.pdf import RenderError
from proposal_engine.pipeline import describe_totals, render_document, report_lines
from proposal_engine.schema import ProposalError, load_proposal
from proposal_engine.theme import load_theme


def page_numbers(text: str) -> list[int]:
    return [int(part) for part in text.split(",") if part.strip()]


def save_deposit_link(path: Path, url: str) -> None:
    """Write the link into proposal.json without touching anything else in the file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data["deposit_link"] = url
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("proposal", type=Path, help="path to proposal.json")
    ap.add_argument("--theme", default=os.environ.get("PROPOSAL_THEME", "ycat"),
                    help="theme name in themes/ or a theme folder (default: ycat)")
    ap.add_argument("-o", "--out", type=Path, help="PDF path (default: out/<slug>.pdf); the HTML goes next to it")
    ap.add_argument("--check", action="store_true", help="validate and print totals, render nothing")
    ap.add_argument("--allow-overflow", action="store_true", help="exit 0 even when a page overflows")
    ap.add_argument("--preview-dir", type=Path, help="also save PNG previews of some pages into this folder")
    ap.add_argument("--preview-pages", default="1,2", help="pages for --preview-dir (default: 1,2)")
    ap.add_argument("--chromium", help="Chromium binary (default: $PROPOSAL_CHROMIUM, else Playwright's bundled Chromium)")
    ap.add_argument("--stripe", action="store_true", help="real mode: create a Stripe payment link for the deposit")
    ap.add_argument("--stripe-dry-run", action="store_true", help="print the Stripe request instead of sending it")
    ap.add_argument("--drive", action="store_true", help="real mode: upload the PDF to Google Drive")
    ap.add_argument("--share", action="store_true", help="with --drive: anyone with the link can view the PDF")
    args = ap.parse_args(argv)

    try:
        proposal = load_proposal(args.proposal)
        theme = load_theme(args.theme)
    except ProposalError as err:
        print(f"INVALID {args.proposal}")
        for problem in err.problems:
            print(f"  - {problem}")
        return 1

    print(f"{proposal.client.company} | {proposal.mode} | theme {theme.name}")
    print(describe_totals(proposal))

    if args.stripe_dry_run:
        from proposal_engine.integrations.stripe_deposit import StripeError, deposit_link_request

        try:
            request = deposit_link_request(proposal, totals_for(proposal), theme.agency)
        except StripeError as err:
            print(f"stripe:  {err}")
        else:
            print("stripe:  dry run, this is what --stripe would send:")
            print(json.dumps(request, indent=2, ensure_ascii=False))
    if args.check:
        print("ok: schema, house style and totals check out")
        return 0

    failures: list[str] = []
    if args.stripe:
        from proposal_engine.integrations.stripe_deposit import (
            StripeError,
            create_deposit_link,
            deposit_link_request,
        )

        if proposal.mode != "direct":
            print("stripe:  skipped, marketplace mode never gets an off-platform payment link")
        elif proposal.deposit_link:
            print(f"stripe:  reusing the deposit link already in {args.proposal.name}")
        else:
            try:
                request = deposit_link_request(proposal, totals_for(proposal), theme.agency)
                url = create_deposit_link(request, os.environ.get("STRIPE_API_KEY", ""))
            except StripeError as err:
                failures.append(f"stripe: {err}")
                print(f"stripe:  FAILED, {err}")
            else:
                save_deposit_link(args.proposal, url)
                proposal = proposal.model_copy(update={"deposit_link": url})
                print(f"stripe:  deposit link created and saved to {args.proposal.name}: {url}")

    pdf_path = args.out or Path("out") / f"{proposal.effective_slug}.pdf"
    previews = []
    if args.preview_dir:
        previews = [(n, args.preview_dir / f"page-{n}.png") for n in page_numbers(args.preview_pages)]
    try:
        html_path, report = render_document(proposal, theme, pdf_path, chromium=args.chromium, previews=previews)
    except RenderError as err:
        print(f"RENDER FAILED: {err}")
        return 3

    for line in report_lines(report):
        print(line)
    print(f"pdf:     {report.pdf_path}")
    print(f"html:    {html_path.resolve()}")
    for preview in report.previews:
        print(f"preview: {preview}")

    if report.problems:
        for problem in report.problems:
            print(f"  OVERFLOW {problem}")
        if not args.allow_overflow:
            print("Shorten the copy on those pages and render again, or pass --allow-overflow.")
            if args.drive:
                print("drive:   skipped, the PDF did not pass the layout check")
            return 2

    if args.drive:
        from proposal_engine.integrations.drive_upload import upload_pdf

        try:
            link = upload_pdf(report.pdf_path, proposal.effective_slug, share=args.share)
        except Exception as err:  # auth, HTTP and quota errors come from several google packages
            failures.append(f"drive: {err}")
            print(f"drive:   FAILED, {err}")
        else:
            links_path = report.pdf_path.with_suffix(".links.json")
            links_path.write_text(json.dumps({"pdf": link, "deposit": proposal.deposit_link}, indent=2) + "\n")
            print(f"drive:   {link} ({'anyone with the link' if args.share else 'private'})")

    return 4 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
