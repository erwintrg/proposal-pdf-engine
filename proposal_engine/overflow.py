"""Per-page overflow check.

Every page is a fixed-height A4 <section> with overflow hidden, so too much copy does not push
content onto a new sheet: it is silently cut off, or it slides under the footer. This check measures
the lowest element on each page in the live layout and compares it with the bottom of the usable
area (the top of the footer, or the bottom padding on pages without a footer). Clipped elements
still report their real position, so cut-off text is found too.
"""

from __future__ import annotations

from dataclasses import dataclass

PX_PER_MM = 96 / 25.4

MEASURE_JS = """
(gapPx) => {
  const pages = [...document.querySelectorAll('section.page')];
  return pages.map((page, i) => {
    const box = page.getBoundingClientRect();
    const foot = page.querySelector('.pfoot');
    const footVisible = foot && getComputedStyle(foot).display !== 'none';
    const limit = footVisible
      ? foot.getBoundingClientRect().top - gapPx
      : box.bottom - parseFloat(getComputedStyle(page).paddingBottom);
    let lowest = box.top, culprit = null;
    for (const el of page.querySelectorAll('*')) {
      if (foot && (el === foot || foot.contains(el))) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      if (r.bottom > lowest) { lowest = r.bottom; culprit = el; }
    }
    return {
      index: i + 1,
      label: page.dataset.label || '',
      fields: page.dataset.fields || '',
      overflowPx: lowest - limit,
      snippet: culprit ? (culprit.innerText || culprit.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 70) : '',
    };
  });
}
"""


@dataclass(frozen=True)
class PageMeasure:
    index: int
    label: str
    fields: str
    overflow_mm: float  # positive = content reaches past the usable area
    snippet: str

    def describe(self) -> str:
        return (f"page {self.index} ({self.label}) overflows by {self.overflow_mm:.1f} mm, "
                f"last visible text: \"{self.snippet}\". Shorten: {self.fields}")


def measure_pages(page, gap_mm: float = 1.5) -> list[PageMeasure]:
    """page: a Playwright page with the proposal HTML loaded and fonts ready."""
    rows = page.evaluate(MEASURE_JS, gap_mm * PX_PER_MM)
    return [
        PageMeasure(r["index"], r["label"], r["fields"], round(r["overflowPx"] / PX_PER_MM, 2), r["snippet"])
        for r in rows
    ]


def find_overflows(measures: list[PageMeasure], tolerance_mm: float = 0.5) -> list[PageMeasure]:
    return [m for m in measures if m.overflow_mm > tolerance_mm]
