"""HTML -> PDF with headless Chromium (Playwright), plus the layout checks and PNG previews.

Chromium: Playwright's bundled build by default (`python -m playwright install chromium`).
Set PROPOSAL_CHROMIUM (or pass `chromium=`) to use another binary, for example /usr/bin/chromium.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, Sequence

from .overflow import PageMeasure, find_overflows, measure_pages

A4_VIEWPORT = {"width": 794, "height": 1123}  # 210 x 297 mm at 96 dpi


class RenderError(Exception):
    pass


@dataclass
class RenderReport:
    pdf_path: Path
    sections: int
    pdf_pages: int
    measures: list[PageMeasure]
    font_errors: list[str] = field(default_factory=list)
    previews: list[Path] = field(default_factory=list)

    @property
    def overflows(self) -> list[PageMeasure]:
        return find_overflows(self.measures)

    @property
    def problems(self) -> list[str]:
        out = [m.describe() for m in self.overflows]
        if self.pdf_pages != self.sections:
            out.append(f"the PDF has {self.pdf_pages} pages but the document has {self.sections} sections, "
                       "a page break leaked (check custom CSS)")
        return out


def chromium_path(explicit: Optional[str] = None) -> Optional[str]:
    return explicit or os.environ.get("PROPOSAL_CHROMIUM") or None


def launch_chromium(playwright, explicit: Optional[str] = None):
    executable = chromium_path(explicit)
    try:
        if executable:
            return playwright.chromium.launch(executable_path=executable)
        return playwright.chromium.launch()
    except Exception as err:  # playwright raises its own Error type; keep the message, add the fix
        hint = (f"Chromium at {executable!r} did not start." if executable else
                "Playwright's bundled Chromium is not installed. Run `python -m playwright install chromium`, "
                "or set PROPOSAL_CHROMIUM to a Chromium binary (for example /usr/bin/chromium).")
        first_line = str(err).strip().splitlines()[0] if str(err).strip() else type(err).__name__
        raise RenderError(f"{hint}\n  ({first_line})") from None


@contextmanager
def open_document(html_path: Path, *, chromium: Optional[str] = None, scale: float = 1.0) -> Iterator[object]:
    """Yield a Playwright page with the document loaded in print media and all fonts settled."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = launch_chromium(pw, chromium)
        try:
            context = browser.new_context(viewport=A4_VIEWPORT, device_scale_factor=scale)
            page = context.new_page()
            page.emulate_media(media="print")
            page.goto(Path(html_path).resolve().as_uri(), wait_until="load")
            page.evaluate("() => document.fonts.ready.then(() => true)")
            yield page
        finally:
            browser.close()


def measure_html(html_path: Path, *, chromium: Optional[str] = None) -> list[PageMeasure]:
    """Layout check only, no PDF: used by the drafter to feed overflows into its repair round."""
    with open_document(html_path, chromium=chromium) as page:
        return measure_pages(page)


def count_pdf_pages(pdf_path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(pdf_path)).pages)


def render_pdf(
    html_path: Path,
    pdf_path: Path,
    *,
    chromium: Optional[str] = None,
    previews: Sequence[tuple[int, Path]] = (),
    preview_scale: float = 1.5,
) -> RenderReport:
    """Render html_path to pdf_path, measure every page, optionally screenshot pages to PNG.

    previews: (page number starting at 1, output path) pairs.
    """
    pdf_path = Path(pdf_path).resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with open_document(html_path, chromium=chromium, scale=preview_scale) as page:
        font_errors = page.evaluate(
            "() => [...document.fonts].filter(f => f.status === 'error').map(f => `${f.family} ${f.style} ${f.weight}`)"
        )
        measures = measure_pages(page)
        sections = len(measures)
        page.pdf(
            path=str(pdf_path), format="A4", print_background=True, prefer_css_page_size=True,
            margin={"top": "0mm", "right": "0mm", "bottom": "0mm", "left": "0mm"},
        )
        written = []
        for number, out_path in previews:
            if not 1 <= number <= sections:
                raise RenderError(f"preview page {number} does not exist (the document has {sections} pages)")
            out_path = Path(out_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            page.locator("section.page").nth(number - 1).screenshot(path=str(out_path))
            written.append(out_path)
    return RenderReport(
        pdf_path=pdf_path,
        sections=sections,
        pdf_pages=count_pdf_pages(pdf_path),
        measures=measures,
        font_errors=sorted(set(font_errors)),
        previews=written,
    )
