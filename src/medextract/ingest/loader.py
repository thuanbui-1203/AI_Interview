"""Ingest entry point: bytes in, ``Document`` out, typed ``InputError`` on anything unusable.

Cheap checks (size, magic bytes) run before any decoding so a hostile or oversized upload is
rejected without touching PyMuPDF or Pillow.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from medextract.core.document import Document, PageImage
from medextract.errors import EmptyDocument, FileTooLarge
from medextract.ingest.image import render_image
from medextract.ingest.pdf import render_pdf
from medextract.ingest.settings import IngestSettings
from medextract.ingest.sniff import PDF, sniff_mime

# Minimum non-space characters for a page to count as having a real text layer: below this it
# is usually a scanner's stray OCR glyph or a page number, too little to ground evidence against.
TEXT_LAYER_MIN_CHARS = 20


def _render(data: bytes, mime: str, settings: IngestSettings) -> tuple[list[PageImage], list[str]]:
    if mime == PDF:
        return render_pdf(data, settings)
    return render_image(data, mime, settings)


def _check_size(data: bytes, settings: IngestSettings) -> None:
    if not data:
        raise EmptyDocument("The file is empty (0 bytes).")
    if len(data) > settings.max_file_bytes:
        mb = len(data) / (1024 * 1024)
        raise FileTooLarge(f"File is {mb:.1f} MB; the limit is {settings.max_file_mb:g} MB.")


def _has_text_layer(pages: list[PageImage]) -> bool:
    return any(p.text and sum(not c.isspace() for c in p.text) >= TEXT_LAYER_MIN_CHARS for p in pages)


def load_document(
    data: bytes,
    *,
    source_name: str | None = None,
    settings: IngestSettings | None = None,
) -> Document:
    """Validate, sniff and rasterize ``data`` into a ``Document``."""
    settings = settings or IngestSettings()
    _check_size(data, settings)
    mime = sniff_mime(data)
    pages, warnings = _render(data, mime, settings)
    if not pages:
        raise EmptyDocument("The document has no pages.")
    if all(p.blank for p in pages):
        raise EmptyDocument("Every page of the document looks blank.")
    warnings += [f"page {p.index} looks blank" for p in pages if p.blank]
    return Document(
        source_name=source_name,
        sha256=hashlib.sha256(data).hexdigest(),
        mime=mime,
        pages=tuple(pages),
        has_text_layer=_has_text_layer(pages),
        pdf_bytes=data if mime == PDF else None,
        warnings=tuple(warnings),
    )


def load_path(path: str | Path, *, settings: IngestSettings | None = None) -> Document:
    """Convenience wrapper: read a file and use its name as ``source_name``."""
    path = Path(path)
    settings = settings or IngestSettings()
    # Refuse oversize files before reading them into memory.
    if path.stat().st_size > settings.max_file_bytes:
        raise FileTooLarge(f"{path.name} exceeds the {settings.max_file_mb:g} MB limit.")
    return load_document(path.read_bytes(), source_name=path.name, settings=settings)
