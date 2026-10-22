"""Ingest: magic-byte sniffing, PDF/image rasterization, limits and typed errors.

All fixtures are generated on the fly (PyMuPDF / Pillow) so the tests need no binary files.
"""

from __future__ import annotations

import io
import unicodedata
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFilter

from medextract.core.document import Document, PageImage
from medextract.errors import (
    EmptyDocument,
    EncryptedPDF,
    FileTooLarge,
    InputError,
    TooManyPages,
    UnreadablePDF,
    UnsupportedFileType,
)
from medextract.ingest import (
    IngestSettings,
    downscale_page,
    load_document,
    load_path,
    sniff_mime,
)
from medextract.ingest.image import blur_score

VI_TEXT = "Bệnh viện Chợ Rẫy - Giấy ra viện - Bệnh nhân: Nguyễn Thị Ánh"


# --- fixture builders ---------------------------------------------------------------------------
def make_pdf(pages: list[str], **save_kwargs) -> bytes:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()  # A4-ish 595x842 pt
        if text:
            page.insert_htmlbox(pymupdf.Rect(50, 50, 545, 400), text)  # built-in fonts cover Vietnamese
    data = doc.tobytes(**save_kwargs)
    doc.close()
    return data


def make_scanned_pdf(n_blank: int, image: bytes | None = None) -> bytes:
    """PDF without text layer: optionally one image page, then ``n_blank`` empty pages."""
    doc = pymupdf.open()
    if image is not None:
        doc.new_page().insert_image(pymupdf.Rect(0, 0, 595, 842), stream=image)
    for _ in range(n_blank):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def text_image(size: tuple[int, int] = (800, 600), mode: str = "RGB") -> Image.Image:
    """White page with dark 'text' strokes, so it is neither blank nor uniform."""
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    for y in range(40, size[1] - 40, 40):
        draw.text((30, y), "Hospital receipt 15.750.000 VND " * 3, fill="black")
    return img.convert(mode) if mode != "RGB" else img


def encode(img: Image.Image, fmt: str, **kwargs) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def decode(page: PageImage) -> Image.Image:
    img = Image.open(io.BytesIO(page.data))
    img.load()
    return img


def assert_page_ok(page: PageImage, settings: IngestSettings | None = None) -> Image.Image:
    """The encoded bytes decode, match the declared media type and size, and respect the caps."""
    settings = settings or IngestSettings()
    img = decode(page)
    assert Image.MIME[img.format] == page.media_type
    assert page.media_type in ("image/png", "image/jpeg")
    assert img.size == (page.width, page.height)
    assert max(img.size) <= settings.image_max_px
    assert img.width * img.height <= settings.max_pixels
    assert img.mode in ("RGB", "L")
    return img


# --- sniffing -----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (b"%PDF-1.7\n...", "application/pdf"),
        (b"\x00\x00junk before header%PDF-1.4", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "image/png"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 8, "image/jpeg"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"II*\x00" + b"\x00" * 8, "image/tiff"),
        (b"MM\x00*" + b"\x00" * 8, "image/tiff"),
    ],
)
def test_sniff_supported(data: bytes, mime: str) -> None:
    assert sniff_mime(data) == mime


@pytest.mark.parametrize("brand", [b"heic", b"mif1", b"heix"])
def test_sniff_heic_rejected_with_helpful_message(brand: bytes) -> None:
    data = b"\x00\x00\x00\x18ftyp" + brand + b"\x00" * 32
    with pytest.raises(UnsupportedFileType, match="HEIC"):
        load_document(data)


def test_sniff_gif_and_unknown_rejected() -> None:
    with pytest.raises(UnsupportedFileType, match="GIF"):
        load_document(encode(text_image(), "GIF"))
    with pytest.raises(UnsupportedFileType):
        load_document(b"hello, this is plain text, not a document")


def test_png_named_pdf_is_sniffed_as_png(tmp_path: Path) -> None:
    path = tmp_path / "claim.pdf"
    path.write_bytes(encode(text_image(), "PNG"))
    doc = load_path(path)
    assert doc.mime == "image/png"
    assert doc.source_name == "claim.pdf"
    assert doc.pdf_bytes is None
    assert doc.pages[0].text is None


# --- PDF ----------------------------------------------------------------------------------------
def test_pdf_vietnamese_text_layer_is_nfc() -> None:
    data = make_pdf([VI_TEXT])
    doc = load_document(data, source_name="giay_ra_vien.pdf")
    assert isinstance(doc, Document)
    assert doc.mime == "application/pdf"
    assert doc.pdf_bytes == data
    assert doc.has_text_layer
    assert doc.page_count == 1
    page = doc.pages[0]
    assert page.index == 1
    assert "Nguyễn Thị Ánh" in page.text
    assert unicodedata.is_normalized("NFC", page.text)
    assert doc.full_text == page.text
    assert_page_ok(page)


def test_pdf_decomposed_text_is_normalized_to_nfc() -> None:
    decomposed = unicodedata.normalize("NFD", VI_TEXT)
    doc = load_document(make_pdf([decomposed]))
    assert unicodedata.is_normalized("NFC", doc.pages[0].text)
    assert "Chợ Rẫy" in doc.pages[0].text


def test_pdf_multi_page_indices_and_full_text() -> None:
    doc = load_document(make_pdf(["Page one discharge summary text", "Page two follow up instructions"]))
    assert [p.index for p in doc.pages] == [1, 2]
    assert "Page one" in doc.full_text and "Page two" in doc.full_text
    assert doc.full_text.index("Page one") < doc.full_text.index("Page two")
    for page in doc.pages:
        img = assert_page_ok(page)
        assert img.height > img.width  # portrait A4 stays portrait
    assert doc.warnings == ()


def test_pdf_raster_respects_dpi_and_size_caps() -> None:
    # Low DPI binds before the caps: A4 at 72 dpi is 595x842.
    page = load_document(make_pdf([VI_TEXT]), settings=IngestSettings(pdf_dpi=72)).pages[0]
    assert (page.width, page.height) == (595, 842)
    # Default: the megapixel cap binds.
    page = load_document(make_pdf([VI_TEXT])).pages[0]
    assert page.width * page.height <= IngestSettings().max_pixels
    assert page.width * page.height > 1_000_000  # not needlessly small


def test_pdf_short_text_is_not_a_text_layer() -> None:
    doc = load_document(make_pdf(["p. 1"]))
    assert doc.pages[0].text == "p. 1"
    assert not doc.has_text_layer


def test_scanned_pdf_has_no_text_layer() -> None:
    doc = load_document(make_scanned_pdf(0, image=encode(text_image(), "PNG")))
    assert not doc.has_text_layer
    assert doc.pages[0].text is None
    assert not doc.pages[0].blank


def test_encrypted_pdf() -> None:
    data = make_pdf([VI_TEXT], encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    with pytest.raises(EncryptedPDF):
        load_document(data)


def test_corrupt_pdf() -> None:
    with pytest.raises(UnreadablePDF):
        load_document(b"%PDF-1.7\n" + b"\x00garbage" * 50)


def test_too_many_pages() -> None:
    data = make_pdf([f"page {i} text" for i in range(7)])
    with pytest.raises(TooManyPages):
        load_document(data)
    assert load_document(data, settings=IngestSettings(max_pages=7)).page_count == 7


# --- size / emptiness ---------------------------------------------------------------------------
def test_empty_bytes() -> None:
    with pytest.raises(EmptyDocument):
        load_document(b"")


def test_file_too_large() -> None:
    data = encode(text_image(), "PNG")
    tiny = IngestSettings(max_file_mb=len(data) / (1024 * 1024) / 2)
    with pytest.raises(FileTooLarge):
        load_document(data, settings=tiny)


def test_load_path_rejects_oversize_before_reading(tmp_path: Path) -> None:
    path = tmp_path / "big.png"
    path.write_bytes(encode(text_image(), "PNG"))
    with pytest.raises(FileTooLarge):
        load_path(path, settings=IngestSettings(max_file_mb=0.0001))


def test_blank_page_gets_warning() -> None:
    doc = load_document(make_pdf(["Discharge summary page with real text content", ""]))
    assert [p.blank for p in doc.pages] == [False, True]
    assert doc.warnings == ("page 2 looks blank",)


def test_all_blank_pages_is_empty_document() -> None:
    with pytest.raises(EmptyDocument, match="blank"):
        load_document(make_scanned_pdf(2))
    with pytest.raises(EmptyDocument):
        load_document(encode(Image.new("RGB", (400, 300), "white"), "PNG"))


def test_sparse_page_is_not_blank() -> None:
    img = Image.new("L", (1000, 1400), 255)
    ImageDraw.Draw(img).text((100, 100), "Total: 120.000", fill=0)
    assert not load_document(encode(img, "PNG")).pages[0].blank


def test_noisy_blank_scan_is_blank() -> None:
    noisy = Image.effect_noise((1000, 1400), 4).point(lambda v: v + 20)  # gray paper + sensor noise
    page_with_text = encode(text_image(), "PNG")
    tiff = encode(Image.open(io.BytesIO(page_with_text)), "TIFF", save_all=True, append_images=[noisy.convert("RGB")])
    doc = load_document(tiff)
    assert [p.blank for p in doc.pages] == [False, True]
    assert doc.warnings == ("page 2 looks blank",)


def test_undecodable_image_is_input_error() -> None:
    truncated = encode(text_image(), "PNG")[:100]
    with pytest.raises(InputError):
        load_document(truncated)


# --- images -------------------------------------------------------------------------------------
def test_exif_orientation_is_applied() -> None:
    img = text_image((800, 600))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 CW on display
    for fmt in ("PNG", "JPEG"):
        page = load_document(encode(img, fmt, exif=exif)).pages[0]
        assert (page.width, page.height) == (600, 800), fmt
        assert_page_ok(page)


def test_rgba_png_flattened_on_white() -> None:
    img = Image.new("RGBA", (400, 300), (0, 0, 0, 0))  # fully transparent black
    ImageDraw.Draw(img).rectangle((50, 50, 150, 150), fill=(0, 0, 0, 255))
    out = assert_page_ok(load_document(encode(img, "PNG")).pages[0]).convert("RGB")
    assert out.getpixel((10, 10)) == (255, 255, 255)  # transparent -> paper white, not black
    assert out.getpixel((100, 100)) == (0, 0, 0)


def test_palette_with_transparency_flattened() -> None:
    img = text_image((300, 200)).convert("P")
    img.info["transparency"] = img.getpixel((0, 0))
    out = assert_page_ok(load_document(encode(img, "PNG", transparency=img.getpixel((0, 0)))).pages[0])
    assert out.convert("RGB").getpixel((0, 0)) == (255, 255, 255)


def test_cmyk_jpeg_converted_to_rgb() -> None:
    page = load_document(encode(text_image(mode="CMYK"), "JPEG")).pages[0]
    img = assert_page_ok(page)
    assert img.mode == "RGB"
    r, g, b = img.getpixel((5, 5))
    assert min(r, g, b) > 200  # white stays white (no inverted-CMYK artefact)


def test_16bit_png_rescaled_not_clipped() -> None:
    img = Image.new("I;16", (400, 300), 65535)  # white in 16 bit
    ImageDraw.Draw(img).rectangle((50, 50, 200, 200), fill=0)
    data = encode(img, "PNG")
    assert Image.open(io.BytesIO(data)).mode.startswith("I")
    out = assert_page_ok(load_document(data).pages[0])
    assert out.mode == "L"
    assert out.getpixel((5, 5)) == 255  # a plain convert("L") would clip everything to 255 or 0
    assert out.getpixel((100, 100)) == 0


def test_one_bit_image_becomes_grayscale() -> None:
    img = text_image().convert("1")
    out = assert_page_ok(load_document(encode(img, "PNG")).pages[0])
    assert out.mode == "L"


def test_webp_supported() -> None:
    doc = load_document(encode(text_image(), "WEBP", quality=90))
    assert doc.mime == "image/webp"
    assert_page_ok(doc.pages[0])


def test_multi_frame_tiff_becomes_pages() -> None:
    frames = [text_image((500, 700)), text_image((700, 500)), text_image((500, 700))]
    data = encode(frames[0], "TIFF", save_all=True, append_images=frames[1:])
    doc = load_document(data)
    assert doc.mime == "image/tiff"
    assert [p.index for p in doc.pages] == [1, 2, 3]
    assert [(p.width, p.height) for p in doc.pages] == [(500, 700), (700, 500), (500, 700)]
    for page in doc.pages:
        assert_page_ok(page)
    with pytest.raises(TooManyPages):
        load_document(data, settings=IngestSettings(max_pages=2))


def test_truncated_multi_frame_tiff_is_input_error() -> None:
    frames = [text_image((400, 500)), text_image((400, 500))]
    data = encode(frames[0], "TIFF", save_all=True, append_images=frames[1:])
    with pytest.raises(InputError):  # frame 2 is cut off; must not escape as a raw OSError
        load_document(data[: int(len(data) * 0.75)])


def test_mpo_phone_jpeg_has_no_animation_warning() -> None:
    img = text_image()
    data = encode(img, "MPO", save_all=True, append_images=[img.resize((200, 150))])
    doc = load_document(data)
    assert doc.mime == "image/jpeg"
    assert len(doc.pages) == 1
    assert doc.warnings == ()


def test_big_image_downscaled_within_limits() -> None:
    page = load_document(encode(text_image((4000, 3000)), "JPEG", quality=85)).pages[0]
    assert_page_ok(page)
    assert abs(page.width / page.height - 4 / 3) < 0.01  # aspect ratio kept
    assert page.width * page.height > 1_100_000


def test_long_edge_cap_binds_for_tall_images() -> None:
    page = load_document(encode(text_image((800, 4000)), "PNG")).pages[0]
    assert page.height == IngestSettings().image_max_px
    assert_page_ok(page)


def test_large_png_falls_back_to_jpeg() -> None:
    noisy = Image.effect_noise((1200, 900), 80).convert("RGB")  # incompressible as PNG
    page = load_document(encode(noisy, "PNG"), settings=IngestSettings(png_max_bytes=200_000)).pages[0]
    assert page.media_type == "image/jpeg"
    assert_page_ok(page)


def test_small_image_not_upscaled() -> None:
    page = load_document(encode(text_image((320, 240)), "PNG")).pages[0]
    assert (page.width, page.height) == (320, 240)
    assert page.media_type == "image/png"


# --- analysis helpers ---------------------------------------------------------------------------
def test_blur_score_drops_when_blurred() -> None:
    sharp = text_image().convert("L")
    blurred = sharp.filter(ImageFilter.GaussianBlur(3))
    assert blur_score(sharp) > 5 * blur_score(blurred)


def test_blur_score_matches_opencv() -> None:
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    gray = Image.effect_noise((300, 200), 40)
    expected = cv2.Laplacian(np.array(gray), cv2.CV_64F)[1:-1, 1:-1].var()
    assert blur_score(gray) == pytest.approx(expected, rel=1e-6)


def test_pages_carry_blur_score() -> None:
    doc = load_document(encode(text_image(), "PNG"))
    assert doc.pages[0].blur_score is not None and doc.pages[0].blur_score > 0


def test_downscale_page_for_classifier() -> None:
    page = load_document(make_pdf([VI_TEXT])).pages[0]
    small = downscale_page(page, 768)
    assert max(small.width, small.height) == 768
    assert small.index == page.index and small.text == page.text
    assert small.media_type == page.media_type
    assert decode(small).size == (small.width, small.height)
    assert downscale_page(small, 1000) is small  # already fits -> no re-encode


def test_document_is_immutable_and_repr_is_small() -> None:
    doc = load_document(make_pdf([VI_TEXT]))
    with pytest.raises(AttributeError):
        doc.mime = "x"  # type: ignore[misc]
    assert len(repr(doc)) < 2000  # image bytes and pdf bytes are excluded from repr
    assert len(doc.sha256) == 64
