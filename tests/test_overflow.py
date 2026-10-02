"""Layout checks in a real Chromium. Skipped when no browser can start (see conftest.chromium)."""

import pytest

from proposal_engine.document import build_html
from proposal_engine.overflow import find_overflows
from proposal_engine.pdf import measure_html
from proposal_engine.pipeline import render_document
from proposal_engine.schema import parse_proposal

pytestmark = pytest.mark.browser

LONG_ITEM = ("One more workflow with its own error handling, tests, documentation and a recorded walkthrough, "
             "so that every scope line wraps onto a second line")


def measure(proposal, theme, tmp_path, chromium):
    html_path = tmp_path / "doc.html"
    html_path.write_text(build_html(proposal, theme), encoding="utf-8")
    return measure_html(html_path, chromium=chromium)


def test_sample_fits_on_every_page_in_both_themes(sample, ycat, neutral, tmp_path, chromium):
    for theme in (ycat, neutral):
        measures = measure(sample, theme, tmp_path, chromium)
        assert [m.label for m in measures][:4] == ["Cover", "The problem", "The solution", "Scope and timeline"]
        assert find_overflows(measures) == [], theme.name


def test_overlong_scope_section_is_reported(sample_data, ycat, tmp_path, chromium):
    sample_data["scope"]["items"] = [f"{LONG_ITEM} ({n})" for n in range(1, 15)]
    overflows = find_overflows(measure(parse_proposal(sample_data), ycat, tmp_path, chromium))
    assert [m.label for m in overflows] == ["Scope and timeline"]
    assert overflows[0].index == 4
    assert overflows[0].overflow_mm > 20
    assert "scope.items" in overflows[0].describe()


def test_overlong_problem_item_is_reported_on_its_own_page(sample_data, ycat, tmp_path, chromium):
    body = sample_data["problem"]["items"][0]["body"]
    sample_data["problem"]["items"][0]["body"] = " ".join([body] * 6)
    overflows = find_overflows(measure(parse_proposal(sample_data), ycat, tmp_path, chromium))
    assert [m.label for m in overflows] == ["The problem"]


def test_text_cut_off_by_the_page_edge_still_counts(tmp_path, chromium):
    """The checker on bare HTML: one page that fits, one whose content runs past the sheet."""
    page = ('<section class="page" data-label="{label}" style="box-sizing:border-box;height:297mm;padding:20mm;overflow:hidden;'
            'position:relative"><div style="height:{h}mm"></div>'
            '<div class="pfoot" style="position:absolute;bottom:9mm;left:20mm;right:20mm">footer</div></section>')
    html = "<!doctype html><body style='margin:0'>" + page.format(label="fits", h=200) + page.format(label="spills", h=400) + "</body>"
    html_path = tmp_path / "bare.html"
    html_path.write_text(html, encoding="utf-8")
    measures = measure_html(html_path, chromium=chromium)
    assert [m.label for m in find_overflows(measures)] == ["spills"]
    assert measures[0].overflow_mm < 0
    assert measures[1].overflow_mm > 100  # 400 mm of content on a 297 mm sheet, measured even though it is clipped


def test_pdf_has_one_page_per_section_and_all_fonts(sample, ycat, tmp_path, chromium):
    _, report = render_document(sample, ycat, tmp_path / "sample.pdf", chromium=chromium,
                                previews=[(1, tmp_path / "p1.png")])
    assert report.sections == report.pdf_pages == 7
    assert report.problems == []
    assert report.font_errors == []
    png = report.previews[0].read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 20_000


def test_layout_problems_flow_into_the_drafter(recorded, notes, ycat, scripted, chromium):
    """An overlong draft gets the overflow report in its repair prompt; the shortened draft passes."""
    import copy
    import datetime as dt
    import json

    from proposal_engine.drafting import draft
    from proposal_engine.pipeline import LayoutChecker

    too_long = copy.deepcopy(recorded)
    too_long["proposal"]["scope"]["items"] = [f"{LONG_ITEM} ({n})" for n in range(1, 15)]
    backend = scripted(json.dumps(too_long), json.dumps(recorded))
    result = draft(notes, backend, ycat, today=dt.date(2026, 10, 1), layout_check=LayoutChecker(ycat, chromium=chromium))
    assert result.attempts == 2
    assert "Scope and timeline" in backend.prompts[1] and "overflows by" in backend.prompts[1]
    assert [p.path for p in result.repaired] == ["layout"]
