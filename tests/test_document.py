import json
import re

import pytest

from proposal_engine.document import build_html, page_plan, rich
from proposal_engine.schema import ProposalError, parse_proposal
from proposal_engine.theme import load_theme


def kinds(proposal):
    return [page.kind for page in page_plan(proposal)]


def test_direct_mode_pages_end_with_the_deposit_invoice(sample, ycat):
    assert kinds(sample) == ["cover", "problem", "solution", "scope", "investment", "agreement", "invoice"]
    html = build_html(sample, ycat)
    assert "D-20261001-ACME-FREIGHT" in html
    assert "€2,400.00" in html and "€456.00" in html and "€2,856.00" in html  # deposit net, VAT, gross
    assert len(re.findall(r'<section class="page \w+-page"', html)) == 7
    assert "7 / 7" in html


def test_marketplace_mode_has_no_invoice_page_and_no_payment_link(sample_data, ycat):
    sample_data["mode"], sample_data["marketplace"] = "marketplace", "Upwork"
    proposal = parse_proposal(sample_data)
    assert "invoice" not in kinds(proposal)
    html = build_html(proposal, ycat)
    assert "All payments run through Upwork" in html
    assert "Due on award" in html
    assert "Deposit invoice" not in html
    assert 'class="btn"' not in html


def test_payment_link_appears_on_investment_and_invoice_pages(sample_data, ycat):
    sample_data["deposit_link"] = "https://buy.stripe.com/test_example"
    html = build_html(parse_proposal(sample_data), ycat)
    assert html.count('href="https://buy.stripe.com/test_example"') == 2


def test_related_page_only_when_there_are_related_systems(sample_data, ycat):
    assert "related" not in kinds(parse_proposal(sample_data))
    sample_data["related"] = [r.model_dump() for r in ycat.agency.related_systems]
    proposal = parse_proposal(sample_data)
    assert kinds(proposal)[4] == "related"
    assert "Automated proposals" in build_html(proposal, ycat)


def test_all_copy_is_escaped(sample_data, ycat):
    sample_data["client"]["company"] = '<script>alert("x")</script> GmbH'
    sample_data["problem"]["items"][0]["body"] = "<img src=x onerror=alert(1)> **bold** stays"
    html = build_html(parse_proposal(sample_data), ycat)
    assert "<script>alert" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html
    assert "<strong>bold</strong> stays" in html


def test_rich_text():
    assert str(rich("**Bold** and *italic*\nnext line\n\nNew paragraph")) == (
        "<p><strong>Bold</strong> and <em>italic</em><br>next line</p>\n<p>New paragraph</p>"
    )
    assert str(rich("")) == ""


def test_theme_values_become_css_variables(sample, ycat, neutral):
    assert "--accent: #827900;" in build_html(sample, ycat)
    neutral_html = build_html(sample, neutral)
    assert "--accent: #2563eb;" in neutral_html
    assert "Example Automation Studio" in neutral_html
    assert "Example Street 1" in neutral_html  # agency address on the invoice and in the imprint


def test_vat_free_proposal_shows_the_label_instead_of_a_vat_line(sample_data, ycat):
    sample_data["vat"] = {"rate": 0, "label": "No VAT: service to a business outside the EU"}
    html = build_html(parse_proposal(sample_data), ycat)
    assert "No VAT: service to a business outside the EU" in html
    assert "Total (net)" not in html


def test_theme_refuses_css_injection(tmp_path):
    theme = json.loads(load_theme("ycat").model_dump_json())
    theme["colors"]["paper"] = "red; } body { display: none"
    folder = tmp_path / "bad"
    folder.mkdir()
    (folder / "theme.json").write_text(json.dumps(theme))
    with pytest.raises(ProposalError) as info:
        load_theme(folder)
    assert any(p.path == "theme.colors.paper" for p in info.value.problems)


def test_theme_reports_missing_files(tmp_path):
    theme = json.loads(load_theme("neutral").model_dump_json())
    theme["fonts"]["stylesheets"].append("local/missing.css")  # relative to the theme folder: not there
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / "theme.json").write_text(json.dumps(theme))  # and no logo.svg next to it
    with pytest.raises(ProposalError) as info:
        load_theme(folder)
    assert {p.path for p in info.value.problems} == {"theme.logo", "theme.fonts.stylesheets"}


def test_a_theme_copied_outside_the_repo_keeps_the_shipped_fonts(tmp_path):
    from proposal_engine import ROOT_DIR

    folder = tmp_path / "my-brand"
    folder.mkdir()
    for name in ("theme.json", "logo.svg"):
        (folder / name).write_bytes((ROOT_DIR / "themes" / "neutral" / name).read_bytes())
    theme = load_theme(folder)
    assert theme.stylesheet_urls() == [(ROOT_DIR / "fonts" / f / "font.css").as_uri() for f in ("dm-sans", "dm-mono")]
    assert theme.logo_url() == (folder / "logo.svg").resolve().as_uri()


def test_unknown_theme_lists_the_available_ones():
    with pytest.raises(ProposalError) as info:
        load_theme("does-not-exist")
    assert "neutral" in str(info.value) and "ycat" in str(info.value)
