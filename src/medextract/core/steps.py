"""The two LLM steps: classify a document, then extract a given document type."""

from __future__ import annotations

from functools import cache
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from medextract import prompts
from medextract.config import Settings
from medextract.core.document import Document
from medextract.core.fields import DocumentLanguage, LLMModel
from medextract.core.models import CallRecord, ClassificationInfo
from medextract.core.registry import DocTypeRegistry, DocTypeSpec
from medextract.ingest import downscale_page
from medextract.providers.base import ImagePart, LLMProvider, PdfPart, StructuredRequest, TextPart

CLASSIFY_MAX_PAGES = 2  # the title block is on page 1; page 2 helps with cover sheets


@cache
def classification_model(names: tuple[str, ...]) -> type[BaseModel]:
    """Classifier output schema, generated from the registered types (cached = byte-stable schema)."""
    scores = create_model(
        "TypeScores",
        __base__=LLMModel,
        **{n: (float, Field(description=f"Probability that the document is a {n} (0-1).")) for n in names},
    )
    return create_model(
        "Classification",
        __base__=LLMModel,
        cues=(str, Field(description="The title/headings/layout cues you relied on, at most 25 words.")),
        is_medical_document=(bool, ...),
        document_language=(DocumentLanguage, ...),
        scores=(scores, Field(description="Probabilities per type, summing to 1.")),
        document_type=(Literal[names], ...),  # type: ignore[valid-type]
    )


def page_parts(doc: Document, *, max_pages: int | None = None, max_px: int | None = None) -> list[Any]:
    pages = doc.pages[:max_pages] if max_pages else doc.pages
    parts: list[Any] = []
    for p in pages:
        img = downscale_page(p, max_px) if max_px else p
        parts.append(TextPart(f"Page {p.index} of {len(doc.pages)}"))
        parts.append(ImagePart(img.media_type, img.data))
    return parts


async def classify(
    doc: Document, registry: DocTypeRegistry, provider: LLMProvider, settings: Settings
) -> tuple[ClassificationInfo, CallRecord]:
    names = tuple(registry.names())
    system = prompts.classify_system(registry.all())
    content = page_parts(doc, max_pages=CLASSIFY_MAX_PAGES, max_px=settings.classify_image_px)
    content.append(TextPart("Classify this document."))
    resp = await provider.structured(
        StructuredRequest(
            stage="classify",
            model=settings.classify_model,
            system=system,
            content=content,
            output_model=classification_model(names),
            max_tokens=settings.max_tokens_classify,
            effort=settings.classify_effort,
            thinking=settings.thinking,
        )
    )
    out = resp.parsed
    scores = _normalized_scores(out.scores.model_dump(), out.document_type)
    info = ClassificationInfo(
        document_type=out.document_type,
        confidence=scores[out.document_type],
        scores=scores,
        language=out.document_language,
        is_medical_document=out.is_medical_document,
        cues=out.cues,
    )
    return info, resp.call


async def extract(
    doc: Document, spec: DocTypeSpec, provider: LLMProvider, settings: Settings
) -> tuple[BaseModel, CallRecord, str]:
    system = prompts.extraction_system(spec)
    if settings.input_mode == "pdf_block" and doc.pdf_bytes:
        content: list[Any] = [PdfPart(doc.pdf_bytes)]
    else:
        content = page_parts(doc)
    content.append(TextPart(prompts.extraction_instruction(spec, len(doc.pages))))
    resp = await provider.structured(
        StructuredRequest(
            stage="extract",
            model=settings.extract_model,
            system=system,
            content=content,
            output_model=spec.schema,
            max_tokens=settings.max_tokens_extract,
            effort=settings.extract_effort,
            thinking=settings.thinking,
        )
    )
    return resp.parsed, resp.call, prompts.prompt_version(system)


def _normalized_scores(raw: dict[str, float], chosen: str) -> dict[str, float]:
    """Clamp to [0,1] and renormalise; the schema cannot enforce either. The chosen type keeps the max."""
    clamped = {k: min(1.0, max(0.0, float(v))) for k, v in raw.items()}
    total = sum(clamped.values())
    if total <= 0:
        return {k: (1.0 if k == chosen else 0.0) for k in clamped}
    scores = {k: round(v / total, 4) for k, v in clamped.items()}
    top = max(scores.values())
    scores[chosen] = max(scores[chosen], top)  # inconsistent self-report: trust the label, not the numbers
    return scores
