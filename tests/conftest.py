import copy
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "acme-freight"


@pytest.fixture(scope="session")
def golden_data() -> dict:
    return json.loads((EXAMPLE / "proposal.json").read_text(encoding="utf-8"))


@pytest.fixture
def sample_data(golden_data) -> dict:
    """A fresh, mutable copy of the fictional sample proposal."""
    return copy.deepcopy(golden_data)


@pytest.fixture
def sample(sample_data):
    from proposal_engine.schema import parse_proposal

    return parse_proposal(sample_data)


@pytest.fixture(scope="session")
def notes() -> str:
    return (EXAMPLE / "notes.md").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def recorded() -> dict:
    return json.loads((EXAMPLE / "recorded-draft.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def ycat():
    from proposal_engine.theme import load_theme

    return load_theme("ycat")


@pytest.fixture(scope="session")
def neutral():
    from proposal_engine.theme import load_theme

    return load_theme("neutral")


@pytest.fixture(scope="session")
def chromium():
    """Browser tests run when Chromium starts (Playwright's bundled one, or PROPOSAL_CHROMIUM)."""
    from playwright.sync_api import sync_playwright

    from proposal_engine.pdf import RenderError, launch_chromium

    try:
        with sync_playwright() as pw:
            launch_chromium(pw).close()
    except RenderError as err:
        pytest.skip(f"no Chromium available: {err}")
    return None  # None = use the default resolution (PROPOSAL_CHROMIUM, else bundled)


class ScriptedBackend:
    """Returns prepared answers in order and records every prompt it was sent."""

    name = "scripted"

    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def complete(self, system: str, user: str, schema: dict) -> str:
        self.prompts.append(user)
        return self.answers.pop(0)


@pytest.fixture
def scripted():
    return ScriptedBackend
