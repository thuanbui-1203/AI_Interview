"""Runtime configuration and the model price table.

Every knob is overridable from the environment with the ``MEDX_`` prefix (or a ``.env`` file),
so an eval run can switch model, effort or strategy without touching code. The Anthropic API key
is deliberately *not* a setting: the SDK reads ``ANTHROPIC_API_KEY`` itself, which keeps the
secret out of anything we might log or dump (``Settings.model_dump()`` is safe to print).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from medextract.core.models import CallRecord

log = logging.getLogger(__name__)

Effort = Literal["low", "medium", "high", "xhigh", "max"]
ThinkingMode = Literal["adaptive", "off"]


class ModelPrice(BaseModel):
    """USD per million tokens. ``cache_write`` is the 5-minute-TTL write price."""

    input: float
    output: float
    cache_write: float
    cache_read: float


# Prices as of 2026-10, override via config (MEDX_PRICE_OVERRIDES, JSON) when they change.
DEFAULT_PRICES: dict[str, ModelPrice] = {
    "claude-opus-5-5": ModelPrice(input=4.0, output=20.0, cache_write=5.0, cache_read=0.20),
    "claude-sonnet-5-5": ModelPrice(input=2.0, output=10.0, cache_write=2.5, cache_read=0.20),
    "claude-haiku-5-5": ModelPrice(input=0.10, output=0.50, cache_write=0.125, cache_read=0.01),
    # No Gemini prices on purpose: development runs on the free tier ($0), and we do not guess 3.5 list
    # prices. Add them via MEDX_PRICE_OVERRIDES to get production cost estimates; tokens are always logged.
}

# qwen3.8-27b is Groq's generally available vision model with strict structured outputs (Groq docs, 2026-10).
GROQ_DEFAULT_MODELS = {"classify_model": "qwen/qwen3.8-27b", "extract_model": "qwen/qwen3.8-27b"}

# Used when MEDX_PROVIDER=gemini and the models were not set explicitly.
# Verified live on 2026-10-22 (2.5 models return 404 "no longer available to new users").
GEMINI_DEFAULT_MODELS = {"classify_model": "gemini-3.5-flash-lite", "extract_model": "gemini-3.5-flash"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEDX_", env_file=".env", extra="ignore")

    # --- models and call strategy ---
    provider: Literal["anthropic", "gemini", "groq"] = "anthropic"
    classify_model: str = "claude-haiku-5-5"
    extract_model: str = "claude-sonnet-5-5"
    strategy: Literal["two_stage", "single_call"] = "two_stage"
    classify_effort: Effort | None = "low"
    extract_effort: Effort | None = "medium"
    thinking: ThinkingMode = "adaptive"
    input_mode: Literal["raster", "pdf_block"] = "raster"
    classify_image_px: int = 768  # the classifier only needs layout + headings, not fine print

    # --- token budgets and resilience ---
    max_tokens_classify: int = 1024
    max_tokens_extract: int = 8192
    max_tokens_ceiling: int = 32000  # upper bound for the one "doubled max_tokens" retry
    timeout_s: float = 120.0
    sdk_max_retries: int = 3  # 429/5xx/connection retries are the SDK's job, not ours
    parse_retries: int = 1

    # --- Gemini free tier: small per-minute quotas ---
    # Read from GEMINI_API_KEY / GOOGLE_API_KEY (environment or .env). SecretStr: never printed or dumped.
    gemini_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY", "MEDX_GEMINI_API_KEY")
    )
    # Client-side spacing of requests; 0 disables. Kept below the free-tier per-minute quota on purpose:
    # a request rejected with 429/503 still counts against the 20/day quota (observed 2026-10-22).
    gemini_rpm: int = 4
    gemini_rate_limit_retries: int = 5  # 429 retries honouring the API's retryDelay
    gemini_server_error_retries: int = 2  # 5xx retries, 20 s then 40 s apart

    # --- Groq ---
    groq_api_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("GROQ_API_KEY", "MEDX_GROQ_API_KEY"))
    groq_max_images: int = 3  # qwen3.8-27b accepts at most 3 images per request
    groq_image_max_px: int = 1600  # Groq budgets ~2k tokens per image; larger uploads only cost payload
    groq_output_tpm: int = 1000  # free-tier output tokens/minute for qwen3.8-27b; 0 disables pacing
    groq_rate_limit_retries: int = 4  # 429 retries, each waiting for the API's retry-after

    # --- record/replay for offline golden tests ---
    record_mode: Literal["off", "record", "replay", "auto"] = "off"
    cassette_dir: Path = Path("tests/cassettes")

    # --- validation ---
    sum_tolerance: float = 0.05

    # --- ingest ---
    max_pages: int = 6
    pdf_dpi: int = 200
    image_max_px: int = 2048  # long edge; Sonnet/Opus 5.x accept up to 2576 px / 3.75 MP
    image_max_megapixels: float = 3.0  # ~175 dpi on A4: small print stays legible; lower it to save image tokens
    max_file_mb: int = 20

    # --- pricing ---
    price_overrides: dict[str, ModelPrice] = {}

    @model_validator(mode="after")
    def _provider_model_defaults(self) -> Settings:
        """Switching provider switches the default models, but never overrides explicit choices."""
        defaults = {"gemini": GEMINI_DEFAULT_MODELS, "groq": GROQ_DEFAULT_MODELS}.get(self.provider, {})
        for name, model in defaults.items():
            if name not in self.model_fields_set:
                setattr(self, name, model)
        return self


class PricingTable:
    """Turns token usage into USD.

    An unknown model costs 0.0 rather than crashing a run that already paid for its tokens; the
    model is remembered in ``unknown_models`` so reports can flag the cost as incomplete.
    """

    def __init__(self, prices: Mapping[str, ModelPrice] | None = None) -> None:
        self._prices: dict[str, ModelPrice] = dict(DEFAULT_PRICES if prices is None else prices)
        self.unknown_models: set[str] = set()

    @classmethod
    def from_settings(cls, settings: Settings) -> PricingTable:
        return cls({**DEFAULT_PRICES, **settings.price_overrides})

    def price_for(self, model: str) -> ModelPrice | None:
        """Exact id first, then a dated snapshot id (``claude-x-5-5-20261001``) by prefix."""
        if model in self._prices:
            return self._prices[model]
        matches = [k for k in self._prices if model.startswith(k + "-")]
        return self._prices[max(matches, key=len)] if matches else None

    def cost(self, call: CallRecord) -> float:
        price = self.price_for(call.model)
        if price is None:
            if call.model not in self.unknown_models:
                log.warning("no price for model %r; cost reported as 0.0", call.model)
            self.unknown_models.add(call.model)
            return 0.0
        usd = (
            call.input_tokens * price.input
            + call.output_tokens * price.output
            + call.cache_creation_input_tokens * price.cache_write
            + call.cache_read_input_tokens * price.cache_read
        ) / 1_000_000
        return round(usd, 8)
