"""LLM providers behind one protocol: the Claude adapter, a test fake and a record/replay wrapper."""

from __future__ import annotations

from medextract.config import PricingTable, Settings
from medextract.providers.anthropic_provider import AnthropicProvider
from medextract.providers.base import (
    ContentPart,
    ImagePart,
    LLMProvider,
    PdfPart,
    StructuredRequest,
    StructuredResponse,
    TextPart,
)
from medextract.providers.cassette import (
    CassetteMiss,
    RecordReplayProvider,
    cassette_key,
)
from medextract.providers.fake import FakeProvider

__all__ = [
    "AnthropicProvider",
    "CassetteMiss",
    "ContentPart",
    "FakeProvider",
    "ImagePart",
    "LLMProvider",
    "PdfPart",
    "RecordReplayProvider",
    "StructuredRequest",
    "StructuredResponse",
    "TextPart",
    "build_provider",
    "cassette_key",
]


def build_provider(settings: Settings, pricing: PricingTable | None = None) -> LLMProvider:
    """The real provider, wrapped for record/replay when ``settings.record_mode`` asks for it."""
    if settings.record_mode == "replay":
        return RecordReplayProvider(None, settings.cassette_dir, "replay")
    real = AnthropicProvider(settings, pricing)
    if settings.record_mode == "record":
        return RecordReplayProvider(real, settings.cassette_dir, "record")
    return real
