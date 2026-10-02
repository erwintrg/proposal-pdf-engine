import json

import pytest

import draft as draft_cli
import render as render_cli
from conftest import EXAMPLE

GOLDEN = str(EXAMPLE / "proposal.json")


def test_render_check_validates_and_prints_totals(capsys):
    assert render_cli.main([GOLDEN, "--check"]) == 0
    out = capsys.readouterr().out
    assert "total €5,712.00" in out and "deposit 50% €2,856.00" in out


def test_render_check_rejects_bad_copy(tmp_path, golden_data, capsys):
    data = dict(golden_data, subtitle="Fast \u2014 and cheap")
    bad = tmp_path / "proposal.json"
    bad.write_text(json.dumps(data))
    assert render_cli.main([str(bad), "--check"]) == 1
    assert "subtitle: em or en dash found" in capsys.readouterr().out


def test_stripe_dry_run_sends_nothing(capsys, monkeypatch):
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    assert render_cli.main([GOLDEN, "--check", "--stripe-dry-run"]) == 0
    out = capsys.readouterr().out
    assert '"unit_amount": 285600' in out


def test_draft_cli_with_the_mock_backend(tmp_path, golden_data, capsys):
    out = tmp_path / "proposal.json"
    code = draft_cli.main([str(EXAMPLE / "notes.md"), "--backend", "mock", "--date", "2026-10-01", "--sample",
                           "--no-layout-check", "-o", str(out)])
    assert code == 0
    assert json.loads(out.read_text()) == golden_data
    assert "prices from the notes" in capsys.readouterr().out


def test_draft_cli_reports_questions_and_writes_nothing(tmp_path, capsys):
    answer = tmp_path / "answer.json"
    answer.write_text(json.dumps({"status": "needs_input", "questions": ["What price did you quote?"]}))
    out = tmp_path / "proposal.json"
    code = draft_cli.main([str(EXAMPLE / "notes.md"), "--backend", "mock", "--recording", str(answer),
                           "--no-layout-check", "-o", str(out)])
    assert code == 2
    assert not out.exists()
    assert "What price did you quote?" in capsys.readouterr().out


@pytest.mark.browser
def test_render_cli_writes_pdf_and_previews(tmp_path, chromium, capsys):
    pdf = tmp_path / "sample.pdf"
    assert render_cli.main([GOLDEN, "-o", str(pdf), "--preview-dir", str(tmp_path), "--preview-pages", "1,7"]) == 0
    assert pdf.stat().st_size > 50_000
    assert (tmp_path / "page-1.png").exists() and (tmp_path / "page-7.png").exists()
    assert "no overflow" in capsys.readouterr().out
