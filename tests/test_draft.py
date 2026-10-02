import copy
import datetime as dt
import json
import stat
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from proposal_engine.drafting import (
    ClaudeCliBackend,
    DraftError,
    MockBackend,
    NeedsInput,
    draft,
    envelope_schema,
    extract_json,
    numbers_in,
    price_guard,
    related_guard,
    system_prompt,
    vat_for,
)
from proposal_engine.schema import ProposalBody, dump_proposal

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "acme-freight"
TODAY = dt.date(2026, 10, 1)


def body_from(recorded) -> ProposalBody:
    return ProposalBody.model_validate(recorded["proposal"])


def test_mock_draft_reproduces_the_committed_example(notes, ycat, golden_data):
    result = draft(notes, MockBackend(EXAMPLE / "recorded-draft.json"), ycat, today=TODAY, sample=True)
    assert result.attempts == 1 and result.backend == "mock"
    assert json.loads(dump_proposal(result.proposal)) == golden_data
    assert result.proposal.vat.rate == 0.19  # from the VAT rule table, the model never sends a rate


def test_system_prompt_carries_the_rules_and_the_verified_list(ycat):
    prompt = system_prompt(ycat)
    assert "You Can Automate This" in prompt and "Erwin Truong" in prompt
    assert "never invent" in prompt
    assert "- Automated proposals: The system that produced this document" in prompt
    assert "$" not in prompt  # every template placeholder was filled


def test_schema_sent_to_the_model_is_strict():
    schema = envelope_schema()
    unsupported = {"minimum", "maximum", "exclusiveMinimum", "minLength", "maxLength", "maxItems", "default"}

    def walk(node, path="$"):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, path
            for key, value in node.items():
                if path.endswith(".properties"):
                    walk(value, f"{path}.{key}")      # property names are free
                else:
                    assert key not in unsupported, f"{path}.{key}"
                    walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(schema)
    assert schema["properties"]["status"]["enum"] == ["ok", "needs_input"]
    assert "Proposal" not in schema["$defs"]  # only the body: the model never sends date, VAT or links
    assert not {"vat", "date", "deposit_link"} & set(schema["$defs"]["ProposalBody"]["properties"])


@pytest.mark.parametrize("wrap", [
    lambda s: s,
    lambda s: f"```json\n{s}\n```",
    lambda s: f"Here is the proposal:\n{s}\nLet me know.",
    lambda s: json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": s}),
    lambda s: json.dumps({"type": "result", "is_error": False, "result": "", "structured_output": json.loads(s)}),
])
def test_extract_json_handles_every_answer_shape(recorded, wrap):
    assert extract_json(wrap(json.dumps(recorded))) == recorded


def test_extract_json_errors():
    with pytest.raises(DraftError):
        extract_json("no json here")
    with pytest.raises(DraftError):
        extract_json(json.dumps({"type": "result", "is_error": True, "result": "usage limit"}))


def test_numbers_in_reads_both_conventions():
    found = numbers_in("Price: EUR 4,800 net, or 4.800,50 with extras, roughly 4.8k. 2-3 days, 50% deposit.")
    assert {Decimal("4800"), Decimal("4800.5"), Decimal("2"), Decimal("3"), Decimal("50")} <= found


def test_price_guard_catches_an_invented_price(recorded):
    body = body_from(recorded)
    assert price_guard("Price: EUR 4,800 net.", body) == []
    problems = price_guard("We did not talk about money yet.", body)
    assert [p.path for p in problems] == ["pricing.items.0.price"]


def test_price_guard_accepts_a_split_of_the_quoted_total(recorded):
    data = copy.deepcopy(recorded["proposal"])
    data["pricing"]["items"] = [{"name": "Build", "price": 3600, "qty": 1}, {"name": "Handover", "price": 1200, "qty": 1}]
    assert price_guard("Price: EUR 4,800 net in total.", ProposalBody.model_validate(data)) == []


def test_related_guard_only_allows_the_verified_list(recorded, ycat):
    data = copy.deepcopy(recorded["proposal"])
    data["related"] = [{"title": "Automated proposals", "body": ycat.agency.related_systems[0].body}]
    assert related_guard(ProposalBody.model_validate(data), ycat.agency) == []
    data["related"] = [{"title": "AI sales agent", "body": "Booked 40 calls in a week."}]
    assert [p.path for p in related_guard(ProposalBody.model_validate(data), ycat.agency)] == ["related.0"]


@pytest.mark.parametrize("country, rate, label_start", [
    ("DE", 0.19, "VAT 19%"),
    ("de", 0.19, "VAT 19%"),
    ("FR", 0, "Reverse charge"),
    ("US", 0, "No VAT: service to a business outside the EU"),
    ("CH", 0, "No VAT"),
])
def test_vat_rule_table(ycat, country, rate, label_start):
    vat = vat_for(ycat.agency, country)
    assert vat.rate == rate and vat.label.startswith(label_start)


@pytest.mark.parametrize("country", ["", "Germany", "D"])
def test_unknown_country_means_ask(ycat, country):
    assert vat_for(ycat.agency, country) is None


def test_model_questions_stop_the_draft(notes, ycat, scripted):
    backend = scripted(json.dumps({"status": "needs_input", "questions": ["What price did you quote?"], "proposal": None}))
    with pytest.raises(NeedsInput) as info:
        draft(notes, backend, ycat, today=TODAY)
    assert info.value.questions == ["What price did you quote?"]


def test_missing_country_asks_instead_of_guessing_vat(notes, recorded, ycat, scripted):
    answer = copy.deepcopy(recorded)
    answer["proposal"]["client"]["country"] = ""
    with pytest.raises(NeedsInput) as info:
        draft(notes, scripted(json.dumps(answer)), ycat, today=TODAY)
    assert "country" in info.value.questions[0]


def test_one_repair_round_fixes_a_bad_draft(notes, recorded, ycat, scripted):
    bad = copy.deepcopy(recorded)
    bad["proposal"]["subtitle"] = "Proof per delivery \u2014 the same day."
    bad["proposal"]["pricing"]["items"][0]["price"] = 5200
    backend = scripted(json.dumps(bad), json.dumps(recorded))
    result = draft(notes, backend, ycat, today=TODAY)
    assert result.attempts == 2
    assert {p.path for p in result.repaired} == {"subtitle", "pricing.items.0.price"}
    repair_prompt = backend.prompts[1]
    assert "<previous>" in repair_prompt and "em or en dash" in repair_prompt and "5200 does not appear" in repair_prompt


def test_gives_up_after_the_repair_round(notes, recorded, ycat, scripted):
    bad = copy.deepcopy(recorded)
    bad["proposal"]["scope"]["outro"] = "Hosting: TBD"
    with pytest.raises(DraftError) as info:
        draft(notes, scripted(json.dumps(bad), json.dumps(bad)), ycat, today=TODAY)
    assert [p.path for p in info.value.problems] == ["scope.outro"]


def test_malformed_answer_gets_repaired(notes, recorded, ycat, scripted):
    backend = scripted("Sorry, here it is: {not json", json.dumps(recorded))
    assert draft(notes, backend, ycat, today=TODAY).attempts == 2


def test_claude_cli_command_line():
    cmd = ClaudeCliBackend(executable="claude", model="claude-opus-5-5").command("SYSTEM", {"type": "object"})
    assert cmd[1:3] == ["-p", "--output-format"]
    assert cmd[cmd.index("--json-schema") + 1] == '{"type": "object"}'
    assert cmd[cmd.index("--tools") + 1] == ""  # no tools: pure generation
    assert "--no-session-persistence" in cmd and cmd[-2:] == ["--model", "claude-opus-5-5"]


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX shebang script as a fake CLI")
def test_claude_cli_backend_end_to_end_with_a_fake_cli(notes, recorded, ycat, tmp_path, golden_data):
    """A stand-in `claude` that prints the result envelope of `claude -p --output-format json`."""
    fake = tmp_path / "claude"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "sys.stdin.read()\n"
        f"answer = open({str(EXAMPLE / 'recorded-draft.json')!r}).read()\n"
        "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': answer}))\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    result = draft(notes, ClaudeCliBackend(executable=str(fake)), ycat, today=TODAY, sample=True)
    assert json.loads(dump_proposal(result.proposal)) == golden_data


def test_claude_cli_missing_binary_is_a_clear_error(ycat):
    with pytest.raises(DraftError, match="not found"):
        ClaudeCliBackend(executable="/nonexistent/claude").complete("s", "u", {})
