"""Document ingestion: PDF / image bytes -> ``medextract.core.document.Document``."""

from medextract.ingest.image import downscale_page
from medextract.ingest.loader import load_document, load_path
from medextract.ingest.settings import IngestSettings
from medextract.ingest.sniff import SUPPORTED_MIMES, sniff_mime

__all__ = [
    "SUPPORTED_MIMES",
    "IngestSettings",
    "downscale_page",
    "load_document",
    "load_path",
    "sniff_mime",
]
