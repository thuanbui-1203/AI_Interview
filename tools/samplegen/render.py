"""HTML (Jinja2) -> PDF (Playwright Chromium) rendering."""

from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any, Self

from jinja2 import ChainableUndefined, Environment, FileSystemLoader

from samplegen.model import SampleDoc, fmt_date, fmt_money, fmt_qty

TEMPLATES_DIR = Path(__file__).parent / "templates"


def make_env() -> Environment:
    # ChainableUndefined: optional nested keys (d.labels.tax, d.stamp.color) degrade to defaults
    # instead of raising, so templates stay readable without `is defined` guards everywhere.
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=True,
        undefined=ChainableUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(money=fmt_money, fdate=fmt_date, fqty=fmt_qty)
    return env


def render_html(doc: SampleDoc, env: Environment | None = None) -> str:
    env = env or make_env()
    return env.get_template(doc.template).render(doc=doc, d=doc.data)


# For each .page: any in-flow child whose bottom passes the footer line (page bottom - 13mm).
_OVERFLOW_JS = """
() => {
  const mm = 96 / 25.4, bad = [];
  document.querySelectorAll('.page').forEach((page, i) => {
    const limit = page.getBoundingClientRect().bottom - 13 * mm;
    page.querySelectorAll(':scope *').forEach(el => {
      if (el.closest('.footer, .stamp, svg') || getComputedStyle(el).position === 'absolute') return;
      if (el.closest('[style*="position:absolute"], [style*="position: absolute"]')) return;
      const r = el.getBoundingClientRect();
      if (r.height > 0 && r.bottom > limit + 0.5) bad.push(`page ${i + 1} +${((r.bottom - limit) / mm).toFixed(1)}mm: ${(el.textContent || '').trim().slice(0, 40)}`);
    });
  });
  return bad.slice(0, 3);
}
"""


class PdfRenderer:
    """One Chromium instance reused for every document (launching it per doc is slow)."""

    def __enter__(self) -> Self:
        from playwright.sync_api import sync_playwright  # dev dependency: import lazily

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch()
        self._page = self._browser.new_page()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        self._browser.close()
        self._pw.stop()

    def to_pdf(self, html: str, out: Path) -> None:
        self._page.set_content(html, wait_until="load")
        self._page.evaluate("document.fonts.ready.then(() => true)")
        overflow = self._page.evaluate(_OVERFLOW_JS)
        if overflow:
            # .page clips its content, so overflow would silently drop printed text that truth expects.
            raise RuntimeError(f"content overflows the page footer area: {overflow}")
        out.parent.mkdir(parents=True, exist_ok=True)
        pdf_opts: dict[str, Any] = {
            "path": str(out),
            "format": "A4",
            "print_background": True,
            "prefer_css_page_size": True,
            "margin": {"top": "0", "right": "0", "bottom": "0", "left": "0"},
        }
        self._page.pdf(**pdf_opts)
