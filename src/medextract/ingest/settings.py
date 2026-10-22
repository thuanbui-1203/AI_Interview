"""Ingest limits. Plain frozen dataclass so ingest stays usable without the app config layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IngestSettings:
    max_file_mb: float = 20.0
    max_pages: int = 6  # page cap (DESIGN §2); more raises TooManyPages instead of silently truncating
    pdf_dpi: int = 200  # enough for 8pt print; the size caps below usually bind first
    image_max_px: int = 2048  # long edge; Sonnet/Opus 5.x read up to 2576 px / 3.75 MP (older models: 1568 / 1.15)
    # ~175 dpi on an A4 page keeps 8pt print legible; image tokens scale with pixels, so this is a cost knob.
    max_megapixels: float = 3.0
    png_max_bytes: int = 3_500_000  # above this, re-encode as JPEG to stay well under API limits
    jpeg_quality: int = 90
    # Grayscale stddev (0-255) below which a page counts as blank. Kept small on purpose: a false
    # "blank" on a sparse page is worse than missing a noisy scanned blank, because all-blank raises.
    blank_std_threshold: float = 2.5

    @property
    def max_file_bytes(self) -> int:
        return int(self.max_file_mb * 1024 * 1024)

    @property
    def max_pixels(self) -> int:
        return int(self.max_megapixels * 1_000_000)
