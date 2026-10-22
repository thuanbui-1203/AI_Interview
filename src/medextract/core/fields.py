"""Building blocks of the *LLM output* schema.

These models are what Claude fills in via structured outputs. They are deliberately richer than
the public output: every scalar carries a verbatim ``evidence`` quote, a ``status`` and a
discrete confidence ``Bucket``. Property order matters - structured outputs are generated in
schema order, so the model quotes the source first, then decides the value, then rates itself.

Constraints the structured-output JSON schema cannot express (ranges, lengths, positivity) are
enforced later in code (normalization + validation), never here.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class Status(StrEnum):
    present = "present"  # clearly printed and read
    partial = "partial"  # cut off / partially obscured
    illegible = "illegible"  # region exists but cannot be read
    ambiguous = "ambiguous"  # more than one plausible candidate
    not_found = "not_found"  # not on the document at all


class Bucket(StrEnum):
    """Discrete self-reported confidence. Mapped to numbers in code (see confidence module)."""

    certain = "certain"
    high = "high"
    medium = "medium"
    low = "low"
    none = "none"


DocumentLanguage = Literal["en", "vi", "th", "mixed", "other"]


class LLMModel(BaseModel):
    """Base for every model sent as a structured-output schema (additionalProperties: false)."""

    model_config = ConfigDict(extra="forbid")


_EVIDENCE = "Shortest verbatim snippet from the document that contains the value, including its label (max ~80 chars). null if the field is not on the document."
_PAGE = "1-based page number where the evidence was found; null if not found."
_RAW = "The value exactly as printed (same characters, separators, currency symbols). null if not found."


class TextField(LLMModel):
    evidence: str | None = Field(description=_EVIDENCE)
    page: int | None = Field(description=_PAGE)
    status: Status
    value: str | None = Field(description="The text value as printed (keep diacritics, never translate).")
    confidence: Bucket


class DateField(LLMModel):
    evidence: str | None = Field(description=_EVIDENCE)
    page: int | None = Field(description=_PAGE)
    status: Status
    raw: str | None = Field(description=_RAW)
    value: str | None = Field(
        description="Your reading of the date as YYYY-MM-DD. Do NOT fix impossible dates: if the printed date is invalid (e.g. 31/02), still give your literal reading."
    )
    confidence: Bucket


class MoneyField(LLMModel):
    evidence: str | None = Field(description=_EVIDENCE)
    page: int | None = Field(description=_PAGE)
    status: Status
    raw: str | None = Field(description=_RAW)
    value: float | None = Field(
        description="Numeric amount without separators or symbols (e.g. '1.250.000 đ' -> 1250000). Copy what is printed; never compute or correct."
    )
    confidence: Bucket


C = TypeVar("C")


class ChoiceField(LLMModel, Generic[C]):
    """A field whose value maps onto a closed set; ``raw`` keeps the printed wording."""

    evidence: str | None = Field(description=_EVIDENCE)
    page: int | None = Field(description=_PAGE)
    status: Status
    raw: str | None = Field(description=_RAW)
    value: C | None
    confidence: Bucket


R = TypeVar("R", bound=BaseModel)


class ListField(LLMModel, Generic[R]):
    """A table on the document. ``status`` is for the table as a whole; each row self-rates."""

    status: Status
    rows: list[R] = Field(description="One entry per printed row, in reading order. Empty list if the table is not on the document.")


class Row(LLMModel):
    """Base for table rows: row-level evidence/page/confidence instead of per-cell wrappers."""

    evidence: str | None = Field(description="The full printed row as one line, verbatim (abbreviate long descriptions with '...').")
    page: int | None = Field(description=_PAGE)


class Diagnosis(LLMModel):
    description: str | None = Field(description="Diagnosis text as printed.")
    icd10_code: str | None = Field(description="ICD-10 code ONLY if printed on the document; never infer a code from the description.")


class DiagnosisField(LLMModel):
    evidence: str | None = Field(description=_EVIDENCE)
    page: int | None = Field(description=_PAGE)
    status: Status
    primary: Diagnosis | None = Field(description="The main / principal diagnosis (VN: 'Chẩn đoán', 'Bệnh chính').")
    secondary: list[Diagnosis] = Field(description="Secondary diagnoses / comorbidities (VN: 'Bệnh kèm theo'). Empty list if none printed.")
    confidence: Bucket
