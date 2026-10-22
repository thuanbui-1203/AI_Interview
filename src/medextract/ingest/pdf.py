"""PDF -> ``PageImage``s via PyMuPDF, keeping each page's text layer for evidence grounding.

Every page is rasterized (decision D7) so born-digital and scanned PDFs take the same path to the
model; the text layer is a side channel used only to check the model's evidence quotes.
"""

from __future__ import annotations

import math
import unicodedata

import pymupdf
from PIL import Image

from medextract.core.document import PageImage
from medextract.errors import EmptyDocument, EncryptedPDF, TooManyPages, UnreadablePDF
from medextract.ingest.image import build_page
from medextract.ingest.settings import IngestSettings

_POINTS_PER_INCH = 72


def _open(data: bytes) -> pymupdf.Document:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several unrelated types for damaged files
        raise UnreadablePDF(f"PDF could not be opened: {exc}") from exc
    if doc.needs_pass:
        doc.close()
        raise EncryptedPDF("PDF is password-protected; please upload an unlocked copy.")
    return doc


def page_text(page: pymupdf.Page) -> str | None:
    """NFC text layer of one page (PDFs often store decomposed Vietnamese diacritics)."""
    text = unicodedata.normalize("NFC", page.get_text("text")).strip()
    return text or None


def _zoom(page: pymupdf.Page, settings: IngestSettings) -> float:
    """Render scale: ``pdf_dpi``, reduced up front so the raster already fits the size caps.

    Rendering straight at the target size is cheaper than rendering big and resampling, and
    MuPDF anti-aliases vector text at any scale. ``build_page`` still enforces the caps exactly.
    """
    w, h = page.rect.width, page.rect.height  # points, rotation already applied
    zoom = settings.pdf_dpi / _POINTS_PER_INCH
    zoom = min(zoom, settings.image_max_px / max(w, h))
    return min(zoom, math.sqrt(settings.max_pixels / (w * h)))


def _rasterize(page: pymupdf.Page, settings: IngestSettings) -> Image.Image:
    zoom = _zoom(page, settings)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def render_pdf(data: bytes, settings: IngestSettings) -> tuple[list[PageImage], list[str]]:
    """Rasterize every page and extract its text. Returns ``(pages, warnings)``."""
    doc = _open(data)
    try:
        n = doc.page_count
        if n == 0:
            raise EmptyDocument("PDF has no pages.")
        if n > settings.max_pages:
            raise TooManyPages(f"PDF has {n} pages; the limit is {settings.max_pages}.")
        pages = []
        for i, page in enumerate(doc, start=1):
            try:
                img, text = _rasterize(page, settings), page_text(page)
            except Exception as exc:
                raise UnreadablePDF(f"page {i} could not be rendered: {exc}") from exc
            pages.append(build_page(img, i, settings, text=text))
        return pages, []
    finally:
        doc.close()
