"""Shared pieces for document-type modules."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from medextract.core.fields import DocumentLanguage, LLMModel

DocType = Literal["receipt", "discharge_summary", "lab_report", "prescription"]


class ExtractionBase(LLMModel):
    """Every extraction schema starts with these two fields (they are metadata, not output fields)."""

    observed_document_type: DocType = Field(
        description="What this document actually is, judged from its content. Used to cross-check the classifier."
    )
    document_language: DocumentLanguage


CURRENCY_DESCRIPTION = (
    "ISO-4217 code (VND, THB, USD, SGD, ...) if printed or unambiguous from a symbol/word on the document "
    "(₫, đ, VNĐ -> VND; ฿, บาท -> THB). evidence = the symbol/word you relied on."
)
