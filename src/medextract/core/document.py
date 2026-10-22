"""The ingested document: what every later stage (classify, extract, ground) consumes.

Ingest turns arbitrary input bytes into a uniform list of page rasters plus, for PDFs, the text
layer of each page. Both are immutable so a ``Document`` can be shared across strategies,
cached, or replayed without anyone mutating the pages under another stage's feet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PageMediaType = str  # "image/png" | "image/jpeg" - the two formats we send to the vision API


@dataclass(frozen=True)
class PageImage:
    index: int  # 1-based, matches the ``page`` the model reports in evidence
    media_type: PageMediaType
    data: bytes = field(repr=False)  # encoded image, ready to base64 into a request
    width: int
    height: int
    text: str | None  # NFC-normalized PDF text layer of this page; None for images / no text
    blur_score: float | None  # variance of Laplacian on a downscaled gray copy; low = blurry
    blank: bool  # near-uniform page (grayscale stddev below threshold)


@dataclass(frozen=True)
class Document:
    source_name: str | None
    sha256: str  # of the original input bytes - the cache / cassette identity
    mime: str  # sniffed from magic bytes, never from the file name
    pages: tuple[PageImage, ...]
    has_text_layer: bool  # True only if some page has real text (grounding is meaningful)
    # Original PDF bytes, kept for an optional native PDF document-block mode (decision D7).
    pdf_bytes: bytes | None = field(default=None, repr=False)
    warnings: tuple[str, ...] = ()

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def full_text(self) -> str:
        """All page text layers joined, for evidence grounding. Empty when there is no text layer."""
        return "\n\n".join(p.text for p in self.pages if p.text)
