"""Shared pieces for document-type modules."""

from __future__ import annotations

from pydantic import Field

from medextract.core.fields import DocumentLanguage, LLMModel


class ExtractionBase(LLMModel):
    """Every extraction schema starts with these two fields (they are metadata, not output fields)."""

    # Free text on purpose: a closed enum of the built-in types would make every new document type
    # "disagree" with the classifier. The pipeline compares it with the classified type's name.
    observed_document_type: str = Field(
        description="What this document actually is, judged from its content: the requested document type name if it fits, "
        "otherwise the closest of receipt / discharge_summary / lab_report / prescription, or 'other'. Used to cross-check the classifier."
    )
    document_language: DocumentLanguage


CURRENCY_DESCRIPTION = (
    "ISO-4217 code (VND, THB, USD, SGD, ...) if printed or unambiguous from a symbol/word on the document "
    "(₫, đ, VNĐ -> VND; ฿, บาท -> THB). evidence = the symbol/word you relied on."
)
