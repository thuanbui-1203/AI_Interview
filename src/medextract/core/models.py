"""Pipeline result models: a rich internal result and its thin public projection.

``PipelineResult.to_public()`` returns exactly the shape the brief asks for::

    {"document_type", "confidence", "fields": {name: {"value", "confidence"}}, "validation_errors"}

``to_public(verbose=True)`` additionally exposes raw text, evidence, status, the confidence
trace and per-call usage/cost, which is what an assessor (or a reviewer) needs to audit a result.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Severity = Literal["error", "warning", "info"]


class ValidationIssue(BaseModel):
    code: str
    severity: Severity
    fields: list[str] = Field(default_factory=list)
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class Adjustment(BaseModel):
    """One step of the deterministic confidence adjuster chain."""

    reason: str
    before: float
    after: float


class FieldResult(BaseModel):
    value: Any
    confidence: float
    # --- audit data (verbose output only) ---
    status: str | None = None
    raw: str | None = None
    evidence: str | None = None
    page: int | None = None
    model_confidence: str | None = None  # the bucket the model chose
    row_evidence: list[str | None] | None = None  # tables: the verbatim printed line behind each row
    trace: list[Adjustment] = Field(default_factory=list)


class CallRecord(BaseModel):
    stage: str  # "classify" | "extract" | ...
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    stop_reason: str | None = None
    attempts: int = 1
    retry_reasons: list[str] = Field(default_factory=list)  # e.g. ["429", "503", "MAX_TOKENS", "parse"]
    request_id: str | None = None


class ClassificationInfo(BaseModel):
    document_type: str
    confidence: float
    scores: dict[str, float] = Field(default_factory=dict)
    language: str | None = None
    is_medical_document: bool = True
    cues: str | None = None


class RunMeta(BaseModel):
    source_name: str | None = None
    sha256: str | None = None
    mime: str | None = None
    page_count: int = 0
    has_text_layer: bool = False
    strategy: str | None = None
    prompt_version: str | None = None
    spec_version: str | None = None
    calls: list[CallRecord] = Field(default_factory=list)
    timings_ms: dict[str, int] = Field(default_factory=dict)

    @property
    def total_cost_usd(self) -> float:
        return round(sum(c.cost_usd for c in self.calls), 6)


class PipelineResult(BaseModel):
    document_type: str
    confidence: float
    fields: dict[str, FieldResult]
    validation_errors: list[ValidationIssue] = Field(default_factory=list)
    classification: ClassificationInfo | None = None
    meta: RunMeta = Field(default_factory=RunMeta)

    def to_public(self, verbose: bool = False) -> dict[str, Any]:
        if verbose:
            fields = {name: f.model_dump() for name, f in self.fields.items()}
        else:
            fields = {name: {"value": f.value, "confidence": f.confidence} for name, f in self.fields.items()}
        out: dict[str, Any] = {
            "document_type": self.document_type,
            "confidence": self.confidence,
            "fields": fields,
            "validation_errors": [
                i.model_dump(exclude={"details"} if not verbose else set()) for i in self.validation_errors
            ],
        }
        if verbose:
            out["classification"] = self.classification.model_dump() if self.classification else None
            meta = self.meta.model_dump()
            meta["total_cost_usd"] = self.meta.total_cost_usd
            out["meta"] = meta
        return out
