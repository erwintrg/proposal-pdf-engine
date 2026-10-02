"""The proposal.json contract.

Two layers:
- `ProposalBody` is everything that comes out of the discovery call: client, problem, solution, scope,
  milestones, pricing, payment terms. This is what the AI drafter writes.
- `Proposal` adds what code or a human decides: date, VAT, slug, deposit link, invoice number.

Structure and types are checked by pydantic (unknown keys are rejected, so a typo like "scop" fails
loudly). House-style rules that pydantic cannot express live in `lint()`.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

CURRENCIES = ("EUR", "USD", "GBP", "CHF")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NumberedItem(Model):
    title: str = Field(min_length=1)
    body: str = Field(min_length=1, description="2 or 3 sentences. Blank line = new paragraph, **bold**, *italic*.")


class Section(Model):
    headline: str = Field(min_length=1)
    headline_accent: str = Field(default="", description="Second part of the headline, set in the accent style.")
    intro: str = ""
    items: list[NumberedItem] = Field(min_length=1, max_length=6)
    outro: str = ""


class Client(Model):
    first_name: str = Field(min_length=1)
    last_name: str = ""
    company: str = Field(min_length=1)
    email: str = ""
    country: str = Field(default="", description="ISO 3166-1 alpha-2 code, for example DE or US.")
    address: list[str] = Field(default_factory=list, max_length=4)
    vat_id: str = ""


class Scope(Model):
    intro: str = ""
    items: list[str] = Field(min_length=1, max_length=14, description="Countable deliverables.")
    outro: str = ""


class Milestone(Model):
    name: str = Field(min_length=1)
    duration: str = Field(min_length=1, description='For example "4 business days".')


class Milestones(Model):
    intro: str = ""
    items: list[Milestone] = Field(min_length=1, max_length=8)
    note: str = ""


class RelatedSystem(Model):
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)


class LineItem(Model):
    name: str = Field(min_length=1)
    price: float = Field(ge=0, description="Unit price, net, in the proposal currency.")
    qty: int = Field(default=1, ge=1)


class RunningCost(Model):
    name: str = Field(min_length=1)
    price: str = Field(min_length=1, description='Free text, for example "~EUR 20 / month".')
    note: str = ""


class Pricing(Model):
    items: list[LineItem] = Field(min_length=1, max_length=10)
    running_costs: list[RunningCost] = Field(default_factory=list, max_length=6)
    note: str = Field(default="", description="Optional lead text for the investment page.")


class PaymentTerms(Model):
    deposit_pct: float = Field(default=50, gt=0, le=100)
    invoice_due_days: int = Field(default=14, ge=0, le=90)


class Vat(Model):
    rate: float = Field(ge=0, lt=1, description="0.19 for 19%.")
    label: str = ""


class ProposalBody(Model):
    """What the drafter writes from the call notes."""

    mode: Literal["direct", "marketplace"] = Field(
        default="direct",
        description="direct = deposit invoice and optional payment link; marketplace = payments run through a freelance marketplace.",
    )
    marketplace: str = Field(default="", description='Name of the marketplace in marketplace mode, for example "Upwork".')
    currency: Literal["EUR", "USD", "GBP", "CHF"]
    client: Client
    title: str = Field(min_length=1)
    title_accent: str = ""
    subtitle: str = ""
    problem: Section
    solution: Section
    scope: Scope
    milestones: Milestones
    related: list[RelatedSystem] = Field(default_factory=list, max_length=4)
    pricing: Pricing
    payment_terms: PaymentTerms = Field(default_factory=PaymentTerms)

    @model_validator(mode="after")
    def _marketplace_needs_a_name(self) -> "ProposalBody":
        if self.mode == "marketplace" and not self.marketplace:
            raise ValueError('mode "marketplace" needs the marketplace name, for example "marketplace": "Upwork"')
        return self


class Proposal(ProposalBody):
    """The full document."""

    sample: bool = Field(default=False, description="Marks the cover as a sample with a fictional client.")
    slug: str = ""
    date: dt.date
    valid_days: int = Field(default=14, ge=1, le=120)
    vat: Vat
    deposit_link: Optional[str] = None
    invoice_number: str = ""

    @property
    def effective_slug(self) -> str:
        return self.slug or slugify(self.client.company)

    @property
    def effective_invoice_number(self) -> str:
        return self.invoice_number or f"D-{self.date:%Y%m%d}-{self.effective_slug.upper()}"

    @property
    def full_title(self) -> str:
        return f"{self.title} {self.title_accent}".strip()


# Order used when proposal.json is written, so the file reads top to bottom like the document.
KEY_ORDER = (
    "sample", "slug", "date", "valid_days", "mode", "marketplace", "currency", "client",
    "title", "title_accent", "subtitle", "problem", "solution", "scope", "milestones", "related",
    "pricing", "vat", "payment_terms", "deposit_link", "invoice_number",
)

LEGAL_SUFFIXES = {"gmbh", "ag", "ug", "kg", "ltd", "llc", "inc", "bv", "sarl", "sas", "srl", "co", "corp", "plc"}


def slugify(text: str) -> str:
    """'Acme Freight GmbH' -> 'acme-freight', 'Müller & Söhne AG' -> 'muller-sohne'."""
    ascii_text = unicodedata.normalize("NFKD", text.replace("ß", "ss")).encode("ascii", "ignore").decode()
    words = re.sub(r"[^a-z0-9]+", " ", ascii_text.lower()).split()
    while len(words) > 1 and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return "-".join(words) or "proposal"


# ---------------------------------------------------------------- problems + loading

@dataclass(frozen=True)
class Problem:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


class ProposalError(Exception):
    def __init__(self, problems: list[Problem]):
        self.problems = problems
        super().__init__("\n".join(str(p) for p in problems))


def problems_from_validation(err: ValidationError) -> list[Problem]:
    out = []
    for e in err.errors():
        path = ".".join(str(part) for part in e["loc"])
        msg = e["msg"].removeprefix("Value error, ")
        out.append(Problem(path, msg))
    return out


def parse_proposal(data: dict[str, Any], *, run_lint: bool = True) -> Proposal:
    """Validate a proposal dict. Raises ProposalError with every problem found."""
    try:
        proposal = Proposal.model_validate(data)
    except ValidationError as err:
        raise ProposalError(problems_from_validation(err)) from None
    if run_lint:
        problems = lint(proposal)
        if problems:
            raise ProposalError(problems)
    return proposal


def load_proposal(path: Path, *, run_lint: bool = True) -> Proposal:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProposalError([Problem("", f"file not found: {path}")]) from None
    except json.JSONDecodeError as err:
        raise ProposalError([Problem("", f"{path} is not valid JSON: {err}")]) from None
    return parse_proposal(data, run_lint=run_lint)


def _integral_floats_to_int(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _integral_floats_to_int(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_integral_floats_to_int(v) for v in value]
    return value


def dump_proposal(proposal: Proposal) -> str:
    data = _integral_floats_to_int(proposal.model_dump(mode="json"))
    ordered = {key: data[key] for key in KEY_ORDER if key in data}
    ordered.update({k: v for k, v in data.items() if k not in ordered})
    return json.dumps(ordered, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------- lint (house style)

DASHES = re.compile("[\u2013\u2014]")  # en dash, em dash
PLACEHOLDER = re.compile(r"\[[A-Z][A-Z0-9 _/.-]{2,}\]|\b(?:TODO|TBD|FIXME)\b|(?i:lorem ipsum)")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
COUNTRY = re.compile(r"^[A-Z]{2}$")


def iter_strings(value: Any, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from iter_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from iter_strings(v, f"{path}.{i}")


def lint(proposal: ProposalBody) -> list[Problem]:
    """Rules pydantic cannot express. Every client-visible string is checked."""
    problems: list[Problem] = []
    data = proposal.model_dump(mode="json")
    for path, text in iter_strings(data):
        if DASHES.search(text):
            problems.append(Problem(path, "em or en dash found, use '-' or a comma"))
        if PLACEHOLDER.search(text):
            problems.append(Problem(path, f"placeholder left in the copy: {PLACEHOLDER.search(text).group(0)!r}"))
    client = proposal.client
    if client.email and not EMAIL.match(client.email):
        problems.append(Problem("client.email", f"not an email address: {client.email!r}"))
    if client.country and not COUNTRY.match(client.country):
        problems.append(Problem("client.country", f"use a 2-letter ISO code like DE or US, got {client.country!r}"))
    if isinstance(proposal, Proposal) and proposal.deposit_link:
        if proposal.mode == "marketplace":
            problems.append(Problem("deposit_link", "no off-platform payment link in marketplace mode"))
        elif not proposal.deposit_link.startswith("https://"):
            problems.append(Problem("deposit_link", "must be an https:// link"))
    return problems
