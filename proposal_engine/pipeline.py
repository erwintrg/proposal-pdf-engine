"""Glue used by the CLIs and the demo: build the HTML, render the PDF, check layout, describe results."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional, Sequence

from .document import build_html
from .money import format_money, format_pct, totals_for
from .overflow import find_overflows
from .pdf import RenderError, RenderReport, measure_html, render_pdf
from .schema import Problem, Proposal
from .theme import Theme


class LayoutChecker:
    """Lays a draft out in Chromium and reports pages that overflow, so the drafter can shorten them
    in its repair round. If no browser can start, the check switches itself off and says why in
    `unavailable`; render.py still checks the final PDF."""

    def __init__(self, theme: Theme, *, chromium: Optional[str] = None):
        self.theme = theme
        self.chromium = chromium
        self.unavailable: Optional[str] = None

    def __call__(self, proposal: Proposal) -> list[Problem]:
        if self.unavailable:
            return []
        with tempfile.TemporaryDirectory(prefix="proposal-layout-") as tmp:
            html_path = Path(tmp) / "draft.html"
            html_path.write_text(build_html(proposal, self.theme), encoding="utf-8")
            try:
                measures = measure_html(html_path, chromium=self.chromium)
            except RenderError as err:
                self.unavailable = str(err)
                return []
        return [Problem("layout", m.describe()) for m in find_overflows(measures)]


def render_document(
    proposal: Proposal,
    theme: Theme,
    pdf_path: Path,
    *,
    chromium: Optional[str] = None,
    previews: Sequence[tuple[int, Path]] = (),
) -> tuple[Path, RenderReport]:
    """Writes <pdf_path stem>.html next to the PDF, then renders and checks the PDF."""
    pdf_path = Path(pdf_path)
    html_path = pdf_path.with_suffix(".html")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(build_html(proposal, theme), encoding="utf-8")
    return html_path, render_pdf(html_path, pdf_path, chromium=chromium, previews=previews)


def describe_totals(proposal: Proposal) -> str:
    t = totals_for(proposal)
    money = lambda amount: format_money(amount, proposal.currency)  # noqa: E731
    vat = f" incl. {proposal.vat.label or 'VAT'} {money(t.vat)}" if t.vat_rate > 0 else ""
    return (f"total {money(t.gross)}{vat}, net {money(t.net)} | "
            f"deposit {format_pct(t.deposit_pct)} {money(t.deposit_gross)}, balance {money(t.balance_gross)}")


def report_lines(report: RenderReport) -> list[str]:
    lines = [f"pages:   {report.sections} sections, {report.pdf_pages} PDF pages"]
    if report.overflows:
        lines.append(f"layout:  {len(report.overflows)} page(s) overflow")
    else:
        # The cover fills its page by design (flex layout), so the tightest page is looked for after it.
        inner = [m for m in report.measures if m.index > 1] or report.measures
        tightest = max(inner, key=lambda m: m.overflow_mm)
        lines.append(f"layout:  no overflow (tightest: page {tightest.index}, {-tightest.overflow_mm:.1f} mm to spare)")
    lines.append("fonts:   all loaded" if not report.font_errors else
                 "fonts:   failed to load, fallback used: " + ", ".join(report.font_errors))
    return lines
