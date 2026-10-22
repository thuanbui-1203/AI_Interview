"""Provider-neutral request/response types and the ``LLMProvider`` protocol.

The pipeline speaks only these types, so the Anthropic adapter, the test fake and the cassette
recorder are interchangeable. Content parts are plain bytes + metadata (no SDK block dicts) so
that a second provider, or a cassette key, never depends on Anthropic's wire format.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel

from medextract.core.models import CallRecord

ImageMediaType = Literal["image/jpeg", "image/png", "image/gif", "image/webp"]
ThinkingMode = Literal["adaptive", "off"]


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagePart:
    media_type: ImageMediaType
    data: bytes = field(repr=False)  # never let page bytes leak into logs or tracebacks


@dataclass(frozen=True)
class PdfPart:
    data: bytes = field(repr=False)


ContentPart = TextPart | ImagePart | PdfPart


@dataclass(frozen=True)
class StructuredRequest[T: BaseModel]:
    """One structured-output call: a system prompt, one user turn, one output schema."""

    stage: str  # "classify" | "extract" | ... - used for logs, CallRecord and cassette names
    model: str
    system: str
    content: Sequence[ContentPart]
    output_model: type[T]
    max_tokens: int
    effort: str | None = None
    thinking: ThinkingMode = "adaptive"


@dataclass(frozen=True)
class StructuredResponse[T: BaseModel]:
    parsed: T
    call: CallRecord
    raw_text: str  # the JSON text as returned, kept for audit and cassettes


class LLMProvider(Protocol):
    async def structured[T: BaseModel](self, req: StructuredRequest[T]) -> StructuredResponse[T]: ...
