"""Grid of page-1 thumbnails with doc_id captions, for a quick human review of the set."""

from __future__ import annotations

from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

THUMB_W, THUMB_H, CAPTION_H, PAD, COLS = 300, 424, 34, 14, 6


def _thumb(path: Path) -> Image.Image:
    if path.suffix.lower() == ".pdf":
        with pymupdf.open(path) as pdf:
            pix = pdf[0].get_pixmap(dpi=40, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    else:
        img = Image.open(path).convert("RGB")
    img.thumbnail((THUMB_W, THUMB_H), Image.Resampling.LANCZOS)
    return img


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def build_contact_sheet(entries: list[tuple[str, Path]], out: Path) -> None:
    """``entries`` = (caption, document path); missing files are skipped."""
    entries = [(c, p) for c, p in entries if p.exists()]
    rows = max(1, -(-len(entries) // COLS))
    sheet = Image.new("RGB", (COLS * (THUMB_W + PAD) + PAD, rows * (THUMB_H + CAPTION_H + PAD) + PAD), "#d9d9d9")
    draw = ImageDraw.Draw(sheet)
    font = _font(13)
    for i, (caption, path) in enumerate(entries):
        x = PAD + (i % COLS) * (THUMB_W + PAD)
        y = PAD + (i // COLS) * (THUMB_H + CAPTION_H + PAD)
        thumb = _thumb(path)
        sheet.paste(thumb, (x + (THUMB_W - thumb.width) // 2, y + (THUMB_H - thumb.height) // 2))
        draw.rectangle([x, y, x + THUMB_W - 1, y + THUMB_H - 1], outline="#888")
        draw.text((x + 2, y + THUMB_H + 6), caption, fill="#111", font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
