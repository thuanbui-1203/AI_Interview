"""File-type detection from magic bytes.

The extension is never trusted: claim uploads are routinely renamed (a phone photo saved as
``.pdf``), and routing on content is the only way to pick the right decoder.
"""

from __future__ import annotations

from medextract.errors import UnsupportedFileType

PDF = "application/pdf"
PNG = "image/png"
JPEG = "image/jpeg"
WEBP = "image/webp"
TIFF = "image/tiff"

SUPPORTED_MIMES = (PDF, PNG, JPEG, WEBP, TIFF)

# ISO-BMFF brands of HEIF-family stills (iPhone photos default to HEIC).
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif", b"avis"}
_PDF_SCAN_WINDOW = 1024  # the PDF spec tolerates junk before %PDF- within the first 1 KB


def sniff_mime(data: bytes) -> str:
    """Return the MIME type of ``data`` or raise ``UnsupportedFileType``."""
    head = data[:16]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if head.startswith(b"\xff\xd8\xff"):
        return JPEG
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return WEBP
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return TIFF
    if b"%PDF-" in data[:_PDF_SCAN_WINDOW]:
        return PDF
    if head[:6] in (b"GIF87a", b"GIF89a"):
        raise UnsupportedFileType("GIF is not supported; please upload a PDF, PNG, JPEG, WEBP or TIFF.")
    if head[4:8] == b"ftyp" and head[8:12] in _HEIF_BRANDS:
        raise UnsupportedFileType(
            "HEIC/HEIF images are not supported. On iPhone, export as JPEG "
            "(Settings > Camera > Formats > Most Compatible) or convert the photo before uploading."
        )
    raise UnsupportedFileType("Unrecognized file type; please upload a PDF, PNG, JPEG, WEBP or TIFF.")
