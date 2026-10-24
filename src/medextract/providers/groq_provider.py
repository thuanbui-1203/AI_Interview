"""Groq adapter (official ``groq`` SDK, OpenAI-style chat completions): same contract as the others.

Groq specifics that shape this file (from the Groq docs, verified against the installed SDK):

- Vision is limited to a few models (``qwen/qwen3.8-27b`` is the generally available one) and to
  **3 images per request**. Longer documents are rejected up front with ``TooManyPages`` instead
  of a provider 400.
- Each image is budgeted at ~2k input tokens server-side, so pages are re-encoded as JPEG with a
  smaller long edge before sending: same information to the model, a fraction of the payload.
- Structured outputs use ``response_format={"type": "json_schema", "json_schema": {..., "strict": true}}``.
  Strict mode needs every property required and ``additionalProperties: false`` - exactly what
  ``schema_lint`` already enforces on every document type.
- qwen3.8 is a reasoning model: ``reasoning_format="hidden"`` keeps the answer pure JSON (reasoning
  tokens are still billed as completion tokens).
- Free tier limits (read from live rate-limit headers and 429 bodies on 2026-10-24): 1000 requests/day,
  8000 tokens/min, and **1000 output tokens/min** - while one extraction emits ~2.6k output tokens.
  Groq serves the over-budget answer and then rejects the following requests, so retries are ours:
  an ``_OutputBudget`` waits out the output-token debt before each call, a 429 waits for ``retry-after``,
  and the SDK's own (fast) retries are disabled. A daily limit is reported as "daily quota exhausted"
  so the eval harness stops early.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import time
from typing import Any

import groq
import pydantic
from groq import AsyncGroq
from PIL import Image
from pydantic import BaseModel

from medextract.config import PricingTable, Settings
from medextract.core.models import CallRecord
from medextract.errors import (
    OutputTruncated,
    ProviderAuthError,
    ProviderError,
    ProviderUnavailable,
    SchemaParseError,
    TooManyPages,
)
from medextract.providers.base import ImagePart, PdfPart, StructuredRequest, StructuredResponse, TextPart

log = logging.getLogger(__name__)

_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}


def reasoning_effort(mode: str, effort: str | None) -> str | None:
    """Our neutral thinking/effort knobs -> Groq ``reasoning_effort`` (None = let the model decide)."""
    if mode == "off":
        return "none"
    return _EFFORT.get(effort) if effort else None


def fit_image(data: bytes, max_px: int, quality: int = 85) -> bytes:
    """JPEG, long edge <= max_px. The model sees ~2k tokens per image anyway; this only cuts payload."""
    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    if max(img.size) > max_px:
        img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def build_messages(req: StructuredRequest[Any], max_images: int, image_max_px: int) -> list[dict[str, Any]]:
    images = sum(isinstance(p, ImagePart) for p in req.content)
    if images > max_images:
        raise TooManyPages(f"{req.model} on Groq accepts at most {max_images} images per request; this {req.stage} call has {images}")
    parts: list[dict[str, Any]] = []
    for p in req.content:
        if isinstance(p, TextPart):
            parts.append({"type": "text", "text": p.text})
        elif isinstance(p, ImagePart):
            b64 = base64.standard_b64encode(fit_image(p.data, image_max_px)).decode("ascii")
            parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        elif isinstance(p, PdfPart):
            raise ProviderError("Groq has no native PDF input; use MEDX_INPUT_MODE=raster")
        else:
            raise TypeError(f"unsupported content part: {type(p).__name__}")
    return [{"role": "system", "content": req.system}, {"role": "user", "content": parts}]


def build_request_kwargs(req: StructuredRequest[Any], max_tokens: int, settings: Settings) -> dict[str, Any]:
    """Keyword arguments for ``client.chat.completions.create`` (pure, so tests can inspect them)."""
    kwargs: dict[str, Any] = {
        "model": req.model,
        "messages": build_messages(req, settings.groq_max_images, settings.groq_image_max_px),
        "max_completion_tokens": max_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": req.output_model.__name__, "strict": True, "schema": req.output_model.model_json_schema()},
        },
        "reasoning_format": "hidden",
    }
    if (effort := reasoning_effort(req.thinking, req.effort)) is not None:
        kwargs["reasoning_effort"] = effort
    return kwargs


def map_sdk_error(exc: Exception) -> ProviderError:
    """Most specific first, mirroring the Anthropic adapter (same Stainless SDK family)."""
    msg = getattr(exc, "message", None) or str(exc)
    if isinstance(exc, (groq.AuthenticationError, groq.PermissionDeniedError)):
        return ProviderAuthError(f"Groq auth failed: {msg}")
    if isinstance(exc, groq.RateLimitError):
        daily = any(k in msg for k in ("per day", "(RPD)", "(TPD)"))
        return ProviderUnavailable(f"Groq {'daily quota exhausted' if daily else 'rate limited after retries'}: {msg}")
    if isinstance(exc, (groq.BadRequestError, groq.UnprocessableEntityError, groq.NotFoundError)):
        return ProviderError(f"Groq rejected the request: {msg}")
    if isinstance(exc, groq.APIStatusError):
        if exc.status_code >= 500:
            return ProviderUnavailable(f"Groq unavailable after SDK retries ({exc.status_code}): {msg}")
        return ProviderError(f"Groq API error {exc.status_code}: {msg}")
    if isinstance(exc, groq.APIConnectionError):  # includes APITimeoutError
        return ProviderUnavailable(f"cannot reach Groq after SDK retries: {exc}")
    return ProviderError(f"Groq SDK error: {exc}")


class _OutputBudget:
    """Output-tokens-per-minute pacing: each answer's tokens push the next allowed start into the future."""

    def __init__(self, tokens_per_minute: int) -> None:
        self._per_token_s = 60.0 / tokens_per_minute if tokens_per_minute > 0 else 0.0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            delay = self._next - time.monotonic()
        if delay > 0:
            log.info("Groq output-token budget: waiting %.0fs", delay)
            await asyncio.sleep(delay)

    def spend(self, output_tokens: int) -> None:
        self._next = max(time.monotonic(), self._next) + output_tokens * self._per_token_s


SERVER_ERROR_BACKOFF_S = 5.0
MAX_RETRY_WAIT_S = 180.0


def retry_after_s(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    value = response.headers.get("retry-after") if response is not None else None
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


class GroqProvider:
    def __init__(self, settings: Settings, pricing: PricingTable | None = None, client: Any | None = None) -> None:
        self._settings = settings
        self._pricing = pricing or PricingTable.from_settings(settings)
        self._client = client
        self._budget = _OutputBudget(settings.groq_output_tpm)

    @property
    def client(self) -> Any:
        # Lazy: a replay-only run never needs GROQ_API_KEY.
        if self._client is None:
            secret = self._settings.groq_api_key
            key = secret.get_secret_value() if secret else os.environ.get("GROQ_API_KEY")
            if not key:
                raise ProviderAuthError("GROQ_API_KEY is not set (create one at https://console.groq.com/keys)")
            # max_retries=0: the SDK's quick retries only dig the output-token debt deeper (see module doc).
            self._client = AsyncGroq(api_key=key, timeout=self._settings.timeout_s, max_retries=0)
        return self._client

    def _retry_wait(self, exc: Exception, mapped: ProviderError, rate_retries: int, server_retries: int) -> float | None:
        """Seconds to wait before retrying, or None when the error is final."""
        if isinstance(exc, groq.RateLimitError):
            if "daily quota" in str(mapped) or rate_retries >= self._settings.groq_rate_limit_retries:
                return None
            return min(max(retry_after_s(exc) or 30.0, 1.0), MAX_RETRY_WAIT_S)
        transient = isinstance(exc, groq.APIConnectionError) or (isinstance(exc, groq.APIStatusError) and exc.status_code >= 500)
        if transient and server_retries < self._settings.sdk_max_retries:
            return SERVER_ERROR_BACKOFF_S * 2**server_retries
        return None

    async def structured[T: BaseModel](self, req: StructuredRequest[T]) -> StructuredResponse[T]:
        tokens = {"input": 0, "output": 0, "cached": 0}
        max_tokens = req.max_tokens
        attempts = parse_failures = rate_retries = server_retries = 0
        retry_reasons: list[str] = []
        truncation_retried = False
        started = time.perf_counter()
        last: Any = None

        def record() -> CallRecord:
            call = CallRecord(
                stage=req.stage,
                model=req.model,
                input_tokens=tokens["input"],
                output_tokens=tokens["output"],
                cache_read_input_tokens=tokens["cached"],
                latency_ms=round((time.perf_counter() - started) * 1000),
                stop_reason=_finish_reason(last),
                attempts=attempts,
                retry_reasons=list(retry_reasons),
                request_id=getattr(last, "id", None),
            )
            call.cost_usd = self._pricing.cost(call)
            return call

        kwargs_base = build_request_kwargs(req, max_tokens, self._settings)  # validates/encodes images once
        while True:
            attempts += 1
            await self._budget.wait()
            try:
                last = await self.client.chat.completions.create(**{**kwargs_base, "max_completion_tokens": max_tokens})
            except groq.GroqError as exc:
                mapped = map_sdk_error(exc)
                wait = self._retry_wait(exc, mapped, rate_retries, server_retries)
                if wait is None:
                    raise _with_call(mapped, record()) from exc
                if isinstance(exc, groq.RateLimitError):
                    rate_retries += 1
                else:
                    server_retries += 1
                retry_reasons.append(str(getattr(exc, "status_code", "conn")))
                log.info("%s: Groq %s, retrying in %.0fs", req.stage, retry_reasons[-1], wait)
                await asyncio.sleep(wait)
                continue
            before = tokens["output"]
            _add_usage(tokens, getattr(last, "usage", None))
            self._budget.spend(tokens["output"] - before)

            reason = _finish_reason(last)
            if reason == "length":
                grown = min(max_tokens * 2, self._settings.max_tokens_ceiling)
                if truncation_retried or grown <= max_tokens:
                    raise _with_call(OutputTruncated(f"output truncated at max_completion_tokens={max_tokens} ({req.stage})"), record())
                truncation_retried, max_tokens = True, grown
                retry_reasons.append("length")
                continue

            raw_text = _answer_text(last)  # empty content falls through to a parse failure (retried once)
            try:
                parsed = req.output_model.model_validate_json(raw_text)
                return StructuredResponse(parsed=parsed, call=record(), raw_text=raw_text)
            except pydantic.ValidationError as exc:
                log.warning("%s: Groq output failed validation (%d errors)", req.stage, exc.error_count())
                parse_failures += 1
                retry_reasons.append("parse")
                if parse_failures > self._settings.parse_retries:
                    msg = f"{req.stage}: output did not match {req.output_model.__name__} after {attempts} attempts"
                    raise _with_call(SchemaParseError(msg), record()) from exc


def _add_usage(tokens: dict[str, int], usage: Any) -> None:
    """prompt_tokens includes cached tokens; reasoning tokens are part of completion_tokens."""
    if usage is None:
        return
    details = getattr(usage, "prompt_tokens_details", None)
    cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
    tokens["cached"] += cached
    tokens["input"] += max(0, (getattr(usage, "prompt_tokens", 0) or 0) - cached)
    tokens["output"] += getattr(usage, "completion_tokens", 0) or 0


def _finish_reason(resp: Any) -> str | None:
    choices = getattr(resp, "choices", None) or []
    return getattr(choices[0], "finish_reason", None) if choices else None


def _answer_text(resp: Any) -> str:
    choices = getattr(resp, "choices", None) or []
    message = getattr(choices[0], "message", None) if choices else None
    return (getattr(message, "content", None) or "").strip()


def _with_call(exc: ProviderError, call: CallRecord) -> ProviderError:
    exc.call = call  # type: ignore[attr-defined]
    return exc
