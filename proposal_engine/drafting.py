"""Discovery-call notes -> proposal.json, with Claude writing the copy and code checking it.

The model writes words. Code owns everything that must be right:
- the JSON shape (pydantic schema, sent as a structured-output schema and validated again locally),
- prices (every price must appear in the notes, so a price can never be invented),
- VAT (a rule table, the model never picks a rate),
- related systems (only entries from the agency's verified list),
- house style (no em or en dashes, no placeholders),
- layout (optional): the draft is laid out in Chromium and every page that overflows is reported.
If a check fails, the model gets one repair round with the exact problems. If it still fails,
nothing is written.

Backends: `mock` replays a recorded answer (offline demo, tests), `anthropic` calls the Messages API,
`claude-cli` runs `claude -p` (Claude Code login, no API key needed).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from string import Template
from typing import Callable, Literal, Optional, Protocol

from pydantic import Field, ValidationError

from . import PACKAGE_DIR
from .money import compute_totals, dec
from .schema import (
    Model,
    Problem,
    Proposal,
    ProposalBody,
    Vat,
    lint,
    problems_from_validation,
    slugify,
)
from .theme import Agency, Theme

DEFAULT_MODEL = "claude-opus-5-5"
SYSTEM_TEMPLATE = PACKAGE_DIR / "prompts" / "draft_system.md"

EU_COUNTRIES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV",
    "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
}


class DraftEnvelope(Model):
    """What the model returns: a proposal, or the questions that block one."""

    status: Literal["ok", "needs_input"]
    questions: list[str] = Field(default_factory=list)
    proposal: Optional[ProposalBody] = None


class DraftError(Exception):
    def __init__(self, message: str, problems: Optional[list[Problem]] = None, raw: str = ""):
        self.problems = problems or []
        self.raw = raw
        details = "".join(f"\n  - {p}" for p in self.problems)
        super().__init__(message + details)


class NeedsInput(DraftError):
    def __init__(self, questions: list[str]):
        self.questions = questions
        super().__init__("the notes are missing information:" + "".join(f"\n  - {q}" for q in questions))


# ---------------------------------------------------------------- schema for the model

# Keywords the structured-output JSON schema does not accept. They stay enforced locally by pydantic.
_UNSUPPORTED_KEYWORDS = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "maxItems", "default", "title",
}


def strict_json_schema(schema):
    """Pydantic JSON schema -> structured-output schema: unsupported keywords removed,
    every object closed with additionalProperties: false."""
    if isinstance(schema, list):
        return [strict_json_schema(v) for v in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key in _UNSUPPORTED_KEYWORDS:
            continue
        if key == "minItems" and value not in (0, 1):
            continue
        if key in ("properties", "$defs"):
            out[key] = {name: strict_json_schema(sub) for name, sub in value.items()}
        else:
            out[key] = strict_json_schema(value)
    if out.get("type") == "object":
        out["additionalProperties"] = False
    return out


def envelope_schema() -> dict:
    return strict_json_schema(DraftEnvelope.model_json_schema())


# ---------------------------------------------------------------- prompts

def system_prompt(theme: Theme) -> str:
    agency = theme.agency
    related = "\n".join(f"- {r.title}: {r.body}" for r in agency.related_systems) or "(none, leave related empty)"
    return Template(SYSTEM_TEMPLATE.read_text(encoding="utf-8")).substitute(
        agency_name=agency.name, person=agency.person, related_systems=related
    )


def user_prompt(notes: str) -> str:
    return f"Discovery-call notes, between the tags:\n\n<notes>\n{notes.strip()}\n</notes>\n\nReturn the JSON object."


def repair_prompt(notes: str, previous: str, problems: list[Problem]) -> str:
    listed = "\n".join(f"- {p}" for p in problems)
    return (
        f"{user_prompt(notes)}\n\nYour previous answer:\n<previous>\n{previous.strip()}\n</previous>\n\n"
        f"It failed these checks:\n{listed}\n\nReturn the corrected JSON object. Keep everything that passed."
    )


# ---------------------------------------------------------------- parsing model output

def extract_json(text: str) -> dict:
    """Find the JSON object in a model answer: plain JSON, a ```json fence, text around it,
    or the result envelope of `claude -p --output-format json`."""
    text = (text or "").strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        candidate = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1]
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError as err:
            raise DraftError(f"the model did not return valid JSON ({err})", raw=text) from None
    if isinstance(value, dict) and value.get("type") == "result" and ("result" in value or "structured_output" in value):
        if value.get("is_error"):
            raise DraftError(f"claude -p reported an error: {str(value.get('result'))[:300]}", raw=text)
        if isinstance(value.get("structured_output"), dict):
            return value["structured_output"]
        return extract_json(str(value.get("result", "")))
    if not isinstance(value, dict):
        raise DraftError("the model returned JSON, but not an object", raw=text)
    return value


# ---------------------------------------------------------------- deterministic guards

_NUMBER = re.compile(r"(\d[\d.,']*\d|\d)\s?([kK])?(?![\w])")


def numbers_in(text: str) -> set[Decimal]:
    """Every number the notes could mean, under both thousands-separator conventions:
    '4,800' -> 4800 and 4.8; '4.800,50' -> 4800.50; '4.8k' -> 4800."""
    found: set[Decimal] = set()
    for raw, kilo in _NUMBER.findall(text):
        raw = raw.replace("'", "")
        readings = set()
        if "," in raw and "." in raw:
            decimal_sep = "," if raw.rfind(",") > raw.rfind(".") else "."
            thousands_sep = "." if decimal_sep == "," else ","
            readings.add(raw.replace(thousands_sep, "").replace(decimal_sep, "."))
        elif "," in raw or "." in raw:
            sep = "," if "," in raw else "."
            readings.add(raw.replace(sep, ""))              # thousands separator
            if raw.count(sep) == 1:
                readings.add(raw.replace(sep, "."))         # decimal separator
        else:
            readings.add(raw)
        for reading in readings:
            try:
                value = Decimal(reading)
            except InvalidOperation:
                continue
            found.add(value * 1000 if kilo else value)
    return {v.normalize() for v in found}


def price_guard(notes: str, body: ProposalBody) -> list[Problem]:
    """Every line-item price must appear in the notes, or at least the net total must."""
    available = numbers_in(notes)
    missing = [
        (i, item) for i, item in enumerate(body.pricing.items) if dec(item.price).normalize() not in available
    ]
    if not missing:
        return []
    net = compute_totals(body.pricing.items, 0, 50).net
    if net.normalize() in available:
        return []
    return [
        Problem(f"pricing.items.{i}.price",
                f"{item.price:g} does not appear in the notes; prices must come from the notes, never estimated")
        for i, item in missing
    ]


def related_guard(body: ProposalBody, agency: Agency) -> list[Problem]:
    def norm(text: str) -> str:
        return " ".join(text.split()).casefold()

    verified = {(norm(r.title), norm(r.body)) for r in agency.related_systems}
    return [
        Problem(f"related.{i}", f"\"{r.title}\" is not in the agency's verified list; copy an entry exactly or leave related empty")
        for i, r in enumerate(body.related)
        if (norm(r.title), norm(r.body)) not in verified
    ]


def vat_for(agency: Agency, client_country: str) -> Optional[Vat]:
    """B2B VAT rule table for an agency in the EU. None = country unknown, ask the human."""
    country = (client_country or "").upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        return None
    if country == agency.country.upper():
        return Vat(rate=agency.domestic_vat_rate, label=agency.domestic_vat_label)
    if agency.country.upper() in EU_COUNTRIES and country in EU_COUNTRIES:
        return Vat(rate=0, label="Reverse charge: VAT to be accounted for by the recipient")
    if agency.country.upper() in EU_COUNTRIES:
        return Vat(rate=0, label="No VAT: service to a business outside the EU")
    return Vat(rate=0, label="")


def finalize(body: ProposalBody, theme: Theme, today: dt.date, *, sample: bool = False) -> Proposal:
    vat = vat_for(theme.agency, body.client.country)
    if vat is None:
        raise NeedsInput(["Which country is the client based in? The VAT line depends on it."])
    data = body.model_dump()
    data.update(
        sample=sample, slug=slugify(body.client.company), date=today, valid_days=14,
        vat=vat.model_dump(), deposit_link=None, invoice_number="",
    )
    return Proposal.model_validate(data)


# ---------------------------------------------------------------- backends

class Backend(Protocol):
    name: str

    def complete(self, system: str, user: str, schema: dict) -> str: ...


class MockBackend:
    """Replays a recorded model answer. No network, no key: used by the demo and the tests."""

    name = "mock"

    def __init__(self, recording: Optional[Path]):
        if recording is None:
            raise DraftError("the mock backend needs a recorded answer (--recording FILE)")
        self.recording = Path(recording)
        if not self.recording.is_file():
            raise DraftError(f"the mock backend needs a recorded answer, not found: {self.recording}")
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, schema: dict) -> str:
        self.calls.append((system, user))
        return self.recording.read_text(encoding="utf-8")


class AnthropicBackend:
    """Claude through the Messages API with a structured-output schema (needs ANTHROPIC_API_KEY)."""

    name = "anthropic"

    def __init__(self, model: Optional[str] = None, effort: str = "high", max_tokens: int = 32000):
        try:
            import anthropic
        except ImportError:
            raise DraftError("the anthropic package is missing: pip install -r requirements-real.txt") from None
        self._anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("PROPOSAL_MODEL") or DEFAULT_MODEL
        self.effort = effort
        self.max_tokens = max_tokens

    def complete(self, system: str, user: str, schema: dict) -> str:
        anthropic = self._anthropic
        try:
            # Streaming keeps long generations clear of HTTP timeouts. `fallbacks: "default"` lets the
            # API retry a policy refusal on Anthropic's recommended fallback model inside the same call.
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
                system=system,
                messages=[{"role": "user", "content": user}],
            ) as stream:
                message = stream.get_final_message()
        except anthropic.AuthenticationError:
            raise DraftError("Anthropic API: authentication failed, check ANTHROPIC_API_KEY") from None
        except anthropic.RateLimitError:
            raise DraftError("Anthropic API: rate limited, try again in a minute") from None
        except anthropic.APIStatusError as err:
            raise DraftError(f"Anthropic API error {err.status_code}: {err.message}") from None
        except anthropic.APIConnectionError:
            raise DraftError("Anthropic API: could not connect") from None
        if message.stop_reason == "refusal":
            raise DraftError("Claude declined to draft this proposal (stop_reason: refusal)")
        if message.stop_reason == "max_tokens":
            raise DraftError("the draft was cut off at max_tokens")
        text = "".join(block.text for block in message.content if block.type == "text")
        if not text.strip():
            raise DraftError("the response contained no text")
        return text


class ClaudeCliBackend:
    """Claude through `claude -p` (Claude Code in print mode, tools off, no session saved)."""

    name = "claude-cli"

    def __init__(self, model: Optional[str] = None, executable: str = "claude", timeout: int = 900):
        self.executable = shutil.which(executable) or executable
        self.model = model or os.environ.get("PROPOSAL_MODEL") or None
        self.timeout = timeout

    def command(self, system: str, schema: dict) -> list[str]:
        cmd = [
            self.executable, "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
            "--system-prompt", system, "--tools", "", "--no-session-persistence",
        ]
        if self.model:
            cmd += ["--model", self.model]
        return cmd

    def complete(self, system: str, user: str, schema: dict) -> str:
        try:
            proc = subprocess.run(
                self.command(system, schema), input=user, capture_output=True, text=True, timeout=self.timeout
            )
        except FileNotFoundError:
            raise DraftError(f"`{self.executable}` not found; install Claude Code or use --backend anthropic") from None
        except subprocess.TimeoutExpired:
            raise DraftError(f"claude -p did not finish within {self.timeout} s") from None
        if proc.returncode != 0:
            raise DraftError(f"claude -p exited with {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:400]}")
        return proc.stdout


def make_backend(name: str, *, recording: Optional[Path] = None, model: Optional[str] = None) -> Backend:
    if name == "mock":
        return MockBackend(recording)
    if name == "anthropic":
        return AnthropicBackend(model=model)
    if name == "claude-cli":
        return ClaudeCliBackend(model=model)
    raise DraftError(f"unknown backend {name!r} (mock, anthropic, claude-cli)")


# ---------------------------------------------------------------- the loop

@dataclass
class DraftResult:
    proposal: Proposal
    backend: str
    attempts: int
    repaired: list[Problem] = field(default_factory=list)


def check_draft(raw: str, notes: str, theme: Theme) -> tuple[Optional[ProposalBody], list[Problem]]:
    """Parse and check one model answer. Raises NeedsInput when the model asked questions."""
    try:
        envelope = DraftEnvelope.model_validate(extract_json(raw))
    except ValidationError as err:
        return None, problems_from_validation(err)
    except DraftError as err:
        return None, [Problem("", str(err))]
    if envelope.status == "needs_input":
        raise NeedsInput(envelope.questions or ["The model asked for more information but listed no question."])
    if envelope.proposal is None:
        return None, [Problem("proposal", "status is ok but the proposal is missing")]
    body = envelope.proposal
    return body, lint(body) + price_guard(notes, body) + related_guard(body, theme.agency)


def draft(
    notes: str,
    backend: Backend,
    theme: Theme,
    *,
    today: dt.date,
    sample: bool = False,
    max_repairs: int = 1,
    layout_check: Optional[Callable[[Proposal], list[Problem]]] = None,
) -> DraftResult:
    """layout_check (optional): lays the finished draft out and returns overflow problems, which then
    go into the repair round like any other failed check (see pipeline.LayoutChecker)."""
    system = system_prompt(theme)
    schema = envelope_schema()
    prompt = user_prompt(notes)
    first_problems: list[Problem] = []
    raw = ""
    problems: list[Problem] = []
    for attempt in range(1, max_repairs + 2):
        raw = backend.complete(system, prompt, schema)
        body, problems = check_draft(raw, notes, theme)
        if body is not None and not problems:
            proposal = finalize(body, theme, today, sample=sample)
            problems = layout_check(proposal) if layout_check else []
            if not problems:
                return DraftResult(proposal, backend.name, attempt, first_problems)
        first_problems = first_problems or problems
        prompt = repair_prompt(notes, raw, problems)
    raise DraftError("the draft failed its checks after the repair round:", problems, raw=raw)
