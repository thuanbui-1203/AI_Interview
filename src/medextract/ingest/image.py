"""Raster images -> ``PageImage``s, plus the raster helpers shared with the PDF path.

Only geometry and color-mode fixes happen here (rotate, flatten, downscale). We never binarize,
denoise or sharpen: the vision model reads the original pixels better than any cleanup we'd guess.
Pillow only - numpy/opencv are dev dependencies and must not be needed at runtime.
"""

from __future__ import annotations

import array
import io
import math

from PIL import (
    Image,
    ImageFilter,
    ImageOps,
    ImageSequence,
    ImageStat,
    UnidentifiedImageError,
)

from medextract.core.document import PageImage
from medextract.errors import FileTooLarge, InputError, TooManyPages
from medextract.ingest.settings import IngestSettings
from medextract.ingest.sniff import TIFF

PNG_MEDIA = "image/png"
JPEG_MEDIA = "image/jpeg"

_ANALYSIS_PX = 512  # blur/blank are measured on a small copy: cheap and resolution-independent
_INK_DELTA = 48  # a pixel this much darker than the paper (median) on the small copy is ink
# Offset keeps negative responses from clipping to 0 in mode "I"; it cancels out of the variance.
_LAPLACIAN = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=2048)


# --- geometry -----------------------------------------------------------------------------------
def fit_size(width: int, height: int, max_px: int, max_pixels: int | None = None) -> tuple[int, int]:
    """Largest size with the same aspect ratio within the long-edge and total-pixel caps."""
    scale = min(1.0, max_px / max(width, height))
    if max_pixels:
        scale = min(scale, math.sqrt(max_pixels / (width * height)))
    if scale >= 1.0:
        return width, height
    # The epsilon stops float error turning an exact 768.0 into 767.
    return max(1, math.floor(width * scale + 1e-6)), max(1, math.floor(height * scale + 1e-6))


def fit_image(img: Image.Image, max_px: int, max_pixels: int | None = None) -> Image.Image:
    size = fit_size(img.width, img.height, max_px, max_pixels)
    if size == img.size:
        return img
    return img.resize(size, Image.Resampling.LANCZOS)


# --- color modes --------------------------------------------------------------------------------
def _has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA", "RGBa", "La") or (img.mode == "P" and "transparency" in img.info)


def _to_8bit_gray(img: Image.Image) -> Image.Image:
    """16-bit / int / float grayscale -> L. A plain convert("L") would clip, not rescale."""
    if img.mode.startswith("I;16"):
        return img.convert("I").point(lambda v: v / 256).convert("L")
    lo, hi = img.getextrema()
    if hi <= 255 and lo >= 0:
        return img.convert("L")
    span = (hi - lo) or 1
    return img.convert("F").point(lambda v: (v - lo) * 255 / span).convert("L")


def normalize_mode(img: Image.Image) -> Image.Image:
    """Bring any decoded mode to RGB or L, flattening transparency onto white (paper color)."""
    if _has_alpha(img):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    if img.mode in ("RGB", "L"):
        return img
    if img.mode == "1":
        return img.convert("L")
    if img.mode.startswith("I") or img.mode == "F":
        return _to_8bit_gray(img)
    return img.convert("RGB")  # CMYK, YCbCr, LAB, HSV, P without transparency


# --- page analysis ------------------------------------------------------------------------------
def _analysis_copy(img: Image.Image) -> Image.Image:
    gray = img.convert("L")
    return fit_image(gray, _ANALYSIS_PX)


def blur_score(gray: Image.Image) -> float:
    """Variance of the Laplacian (same number as OpenCV's ``Laplacian(...).var()``), Pillow only."""
    if gray.width < 3 or gray.height < 3:
        return 0.0
    lap = gray.convert("I").filter(_LAPLACIAN)
    lap = lap.crop((1, 1, lap.width - 1, lap.height - 1))  # border pixels are padding artefacts
    values = array.array("i")
    values.frombytes(lap.tobytes())
    n = len(values)
    mean = sum(values) / n  # the constant offset shifts the mean but cancels out of the variance
    return max(0.0, sum(v * v for v in values) / n - mean * mean)


def is_blank(gray: Image.Image, threshold: float) -> bool:
    """Low grayscale stddev AND no clearly dark pixel on the small copy.

    Stddev alone cannot tell one short printed line on a big page from scanner noise (both sit
    around 2). Downscaling averages noise away but keeps real ink well below the paper level,
    so the ink check rescues sparse pages without making noisy blank scans count as content.
    """
    stat = ImageStat.Stat(gray)
    darkest = gray.getextrema()[0]
    return stat.stddev[0] < threshold and stat.median[0] - darkest < _INK_DELTA


# --- encoding -----------------------------------------------------------------------------------
def encode(img: Image.Image, settings: IngestSettings) -> tuple[str, bytes]:
    """PNG (lossless, crisp text) unless it is too big, then JPEG q=90 (photos compress poorly as PNG)."""
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=6)
    if buf.tell() <= settings.png_max_bytes:
        return PNG_MEDIA, buf.getvalue()
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=settings.jpeg_quality, optimize=True)
    return JPEG_MEDIA, buf.getvalue()


def build_page(img: Image.Image, index: int, settings: IngestSettings, text: str | None = None) -> PageImage:
    """Normalize, downscale, analyse and encode one page raster."""
    img = fit_image(normalize_mode(img), settings.image_max_px, settings.max_pixels)
    gray = _analysis_copy(img)
    media_type, data = encode(img, settings)
    return PageImage(
        index=index,
        media_type=media_type,
        data=data,
        width=img.width,
        height=img.height,
        text=text,
        blur_score=round(blur_score(gray), 2),
        # A page with a real text layer is never blank, however faint its ink.
        blank=not text and is_blank(gray, settings.blank_std_threshold),
    )


def downscale_page(page: PageImage, max_px: int) -> PageImage:
    """Smaller copy of a page for the cheap classifier call; returns ``page`` itself if it fits."""
    if max(page.width, page.height) <= max_px:
        return page
    with Image.open(io.BytesIO(page.data)) as src:
        img = fit_image(src, max_px)
    buf = io.BytesIO()
    if page.media_type == JPEG_MEDIA:
        img.save(buf, format="JPEG", quality=IngestSettings.jpeg_quality, optimize=True)
    else:
        img.save(buf, format="PNG")
    return PageImage(
        index=page.index,
        media_type=page.media_type,
        data=buf.getvalue(),
        width=img.width,
        height=img.height,
        text=page.text,
        blur_score=page.blur_score,
        blank=page.blank,
    )


# --- decoding -----------------------------------------------------------------------------------
def _open(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()  # surface truncation errors here, not deep inside resize
    except Image.DecompressionBombError as exc:
        raise FileTooLarge(f"image has too many pixels to process safely: {exc}") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise InputError(f"image could not be decoded: {exc}") from exc
    return img


def _frames(img: Image.Image, mime: str, settings: IngestSettings) -> list[Image.Image]:
    """One frame per page for multi-page TIFF; first frame only for everything else."""
    n = getattr(img, "n_frames", 1)
    if mime != TIFF or n == 1:
        return [img]
    if n > settings.max_pages:
        raise TooManyPages(f"TIFF has {n} pages; the limit is {settings.max_pages}.")
    try:  # later frames are decoded lazily here, after _open's load() of frame 1
        return [frame.copy() for frame in ImageSequence.Iterator(img)]
    except (OSError, SyntaxError, ValueError) as exc:
        raise InputError(f"TIFF page could not be decoded: {exc}") from exc


def render_image(data: bytes, mime: str, settings: IngestSettings) -> tuple[list[PageImage], list[str]]:
    """Decode an image file into pages. Returns ``(pages, warnings)``."""
    img = _open(data)
    warnings: list[str] = []
    # Phone JPEGs are often MPO (main photo + depth/preview frame): not animation, no warning.
    if mime != TIFF and img.format != "MPO" and getattr(img, "n_frames", 1) > 1:
        warnings.append("animated image: only the first frame was used")
    pages = [
        build_page(ImageOps.exif_transpose(frame), i, settings)
        for i, frame in enumerate(_frames(img, mime, settings), start=1)
    ]
    return pages, warnings
