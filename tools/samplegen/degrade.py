"""Turn a clean PDF page into a scanned or phone-photographed PNG.

Degradations are seeded per document so regeneration is reproducible. They target what
real claim submissions look like: tinted paper, skew, sensor noise, blur, JPEG artifacts and,
for photos, perspective and uneven lighting.
"""

from __future__ import annotations

import zlib
from pathlib import Path

import cv2
import numpy as np
import pymupdf

PAPER_YELLOW = np.array([206, 236, 247], dtype=np.float32)  # BGR, aged office paper
PAPER_GRAY = np.array([238, 238, 238], dtype=np.float32)


def rng_for(doc_id: str) -> np.random.Generator:
    return np.random.default_rng(zlib.crc32(doc_id.encode()))


def rasterize(pdf_path: Path, dpi: int = 170, page: int = 0) -> np.ndarray:
    """Render one PDF page to a BGR uint8 array."""
    with pymupdf.open(pdf_path) as pdf:
        pix = pdf[page].get_pixmap(dpi=dpi, alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def jpeg_roundtrip(img: np.ndarray, quality: int) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def _rotate(img: np.ndarray, angle: float, border: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    mat = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(img, mat, (w, h), flags=cv2.INTER_LINEAR, borderValue=tuple(float(c) for c in border))


def _tint(img: np.ndarray, paper: np.ndarray, grayscale: bool) -> np.ndarray:
    """Multiply onto paper colour: white becomes paper, ink stays dark."""
    base = img.astype(np.float32)
    if grayscale:
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        base = np.repeat(g[:, :, None], 3, axis=2)
    return base * (paper / 255.0)


def scanify(img: np.ndarray, rng: np.random.Generator, heavy: bool = False) -> np.ndarray:
    grayscale = bool(rng.random() < 0.5) and not heavy
    paper = PAPER_GRAY if grayscale else PAPER_YELLOW
    out = _tint(img, paper, grayscale)
    # faded toner: lift the blacks a little
    lift = 45 if heavy else 22
    out = out * (1 - lift / 255.0) + lift
    angle = float(rng.uniform(2.0, 3.0) if heavy else rng.uniform(1.0, 2.0)) * (1 if rng.random() < 0.5 else -1)
    out = _rotate(np.clip(out, 0, 255).astype(np.uint8), angle, paper * 0.92).astype(np.float32)
    if heavy:
        h, w = out.shape[:2]
        # darker binding edge and a scanner streak
        edge = np.linspace(0.72, 1.0, num=w // 6, dtype=np.float32)
        out[:, : w // 6] *= edge[None, :, None]
        x = int(rng.integers(w // 3, 2 * w // 3))
        out[:, x : x + 2] *= 0.85
        for _ in range(int(h * w / 9000)):  # dust speckles
            cy, cx = int(rng.integers(0, h)), int(rng.integers(0, w))
            cv2.circle(out, (cx, cy), int(rng.integers(1, 3)), (60, 60, 60), -1)
    out = cv2.GaussianBlur(out, (0, 0), 1.1 if heavy else 0.7)
    out += rng.normal(0, 13 if heavy else 7, out.shape).astype(np.float32)
    out = np.clip(out, 0, 255).astype(np.uint8)
    return jpeg_roundtrip(out, 38 if heavy else 62)


def photoify(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Place the page on a table at an angle, with a shadow gradient and phone JPEG quality."""
    h, w = img.shape[:2]
    canvas_w, canvas_h = int(w * 1.25), int(h * 1.18)
    # table background: warm wood-ish gradient with grain
    yy, xx = np.mgrid[0:canvas_h, 0:canvas_w].astype(np.float32)
    grain = 12 * np.sin(xx / 7.0 + 3 * np.sin(yy / 90.0))
    bg = np.stack([60 + grain * 0.5, 92 + grain, 128 + grain], axis=2) + rng.normal(0, 5, (canvas_h, canvas_w, 3))

    def jitter(scale: float) -> float:
        return float(rng.uniform(-scale, scale))

    ox, oy = (canvas_w - w) / 2, (canvas_h - h) / 2
    dst = np.float32([
        [ox + w * 0.06 + jitter(w * 0.03), oy + jitter(h * 0.02)],
        [ox + w * 0.97 + jitter(w * 0.02), oy + h * 0.03 + jitter(h * 0.02)],
        [ox + w * 1.02 + jitter(w * 0.02), oy + h * 0.99 + jitter(h * 0.01)],
        [ox - w * 0.02 + jitter(w * 0.02), oy + h * 0.95 + jitter(h * 0.02)],
    ])  # fmt: skip
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    mat = cv2.getPerspectiveTransform(src, dst)
    paper = cv2.warpPerspective(img, mat, (canvas_w, canvas_h), flags=cv2.INTER_LINEAR)
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), mat, (canvas_w, canvas_h))
    mask_f = cv2.GaussianBlur(mask.astype(np.float32) / 255.0, (0, 0), 1.2)[:, :, None]
    out = paper.astype(np.float32) * mask_f + bg * (1 - mask_f)

    # lighting: diagonal falloff plus a soft hand/phone shadow blob
    light = 0.62 + 0.38 * (xx / canvas_w * 0.6 + (1 - yy / canvas_h) * 0.4)
    blob = np.exp(-(((xx - canvas_w * 0.78) / (canvas_w * 0.28)) ** 2 + ((yy - canvas_h * 0.82) / (canvas_h * 0.22)) ** 2))
    light = light * (1 - 0.35 * blob)
    out = out * light[:, :, None] * np.array([0.93, 0.98, 1.04], dtype=np.float32)  # warm indoor light
    out = cv2.GaussianBlur(out, (0, 0), 1.0)
    out += rng.normal(0, 6, out.shape)
    out = np.clip(out, 0, 255).astype(np.uint8)
    out = cv2.resize(out, (int(canvas_w * 0.8), int(canvas_h * 0.8)), interpolation=cv2.INTER_AREA)
    return jpeg_roundtrip(out, 40)


def degrade_pdf(pdf_path: Path, png_path: Path, mode: str, doc_id: str) -> None:
    rng = rng_for(doc_id)
    dpi = 160 if mode == "photo" else 170
    img = rasterize(pdf_path, dpi=dpi)
    if mode == "photo":
        out = photoify(img, rng)
    else:
        out = scanify(img, rng, heavy=(mode == "heavy_scan"))
    png_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(png_path), out, [cv2.IMWRITE_PNG_COMPRESSION, 9])
