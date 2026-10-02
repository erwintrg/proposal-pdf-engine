"""proposal.json + theme -> one HTML document, one <section class="page"> per A4 page."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup, escape

from . import PACKAGE_DIR
from .money import format_money, format_pct, totals_for
from .schema import Proposal
from .theme import Theme

TEMPLATES = PACKAGE_DIR / "templates"


@dataclass(frozen=True)
class Page:
    kind: str
    label: str
    fields: str  # which proposal.json keys fill this page, used in overflow hints


PAGES = {
    "cover": ("Cover", "title, title_accent, subtitle, client.company"),
    "problem": ("The problem", "problem.headline, problem.intro, problem.items, problem.outro"),
    "solution": ("The solution", "solution.headline, solution.intro, solution.items, solution.outro"),
    "scope": ("Scope and timeline", "scope.intro, scope.items, scope.outro, milestones.items, milestones.note"),
    "related": ("Related systems", "related"),
    "investment": ("Your investment", "pricing.items, pricing.running_costs, pricing.note"),
    "agreement": ("Services agreement", "fixed text, plus the theme's agency details"),
    "invoice": ("Deposit invoice", "client.address, plus the theme's agency address"),
}


def page_plan(proposal: Proposal) -> list[Page]:
    kinds = ["cover", "problem", "solution", "scope"]
    if proposal.related:
        kinds.append("related")
    kinds += ["investment", "agreement"]
    if proposal.mode == "direct":
        kinds.append("invoice")  # marketplace mode: the marketplace invoices, so no invoice page
    return [Page(kind, *PAGES[kind]) for kind in kinds]


def rich(text: str) -> Markup:
    """Plain text to HTML: blank line = new paragraph, single newline = <br>, **bold**, *italic*.
    The text is escaped first, so copy written by a model can never inject markup."""
    paragraphs = []
    for para in re.split(r"\n\s*\n", (text or "").strip()):
        if not para.strip():
            continue
        html = str(escape(para))
        html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
        html = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", html)
        paragraphs.append(f"<p>{html.replace(chr(10), '<br>')}</p>")
    return Markup("\n".join(paragraphs))


def long_date(value: dt.date) -> str:
    return f"{value.day:02d} {value:%B %Y}"


def _environment() -> Environment:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=True, undefined=StrictUndefined)
    env.filters["rich"] = rich
    return env


def build_html(proposal: Proposal, theme: Theme) -> str:
    agency = theme.agency
    totals = totals_for(proposal)
    client = proposal.client
    imprint_parts = [agency.name, agency.person, *agency.address]
    if agency.vat_id:
        imprint_parts.append(f"VAT ID {agency.vat_id}")
    imprint_parts.append(agency.email)
    css = theme.css_variables() + "\n" + (TEMPLATES / "proposal.css").read_text(encoding="utf-8")

    context = {
        "p": proposal,
        "agency": agency,
        "totals": totals,
        "money": lambda amount: format_money(amount, proposal.currency),
        "pct": format_pct(proposal.payment_terms.deposit_pct),
        "marketplace": proposal.mode == "marketplace",
        "pages": page_plan(proposal),
        "client_name": f"{client.first_name} {client.last_name}".strip(),
        "date_long": long_date(proposal.date),
        "valid_until": long_date(proposal.date + dt.timedelta(days=proposal.valid_days)),
        "invoice_number": proposal.effective_invoice_number,
        "imprint": " · ".join(part for part in imprint_parts if part),
        "logo": theme.logo_url(),
        "stylesheets": theme.stylesheet_urls(),
        "css": Markup(css),  # theme values are checked for CSS-breaking characters when the theme loads
    }
    return _environment().get_template("proposal.html.j2").render(**context)
