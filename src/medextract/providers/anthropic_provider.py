"""Claude adapter: one structured-output call with our retry and error policy on top.

Retry ownership is split on purpose. The SDK retries transport-level failures (429, 5xx,
connection, timeout) ``sdk_max_retries`` times. This adapter only retries what the SDK cannot
know about: a truncated answer (``max_tokens``, retried once with double the budget) and an
answer that does not validate against the schema (retried ``parse_retries`` times).

Why the schema goes in ``output_config.format`` instead of ``output_format=``: with
``output_format`` the SDK validates the JSON *inside* ``messages.parse``, so a truncated or
refused answer surfaces as a bare pydantic ``ValidationError`` before we can look at
``stop_reason``. Sending the same transformed schema ourselves lets us check ``stop_reason``
first and then validate - the wire request is identical.
"""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import anthropic
import pydantic
from anthropic import AsyncAnthropic, transform_schema
from pydantic import BaseModel

from medextract.config import PricingTable, Settings
from medextract.core.models import CallRecord
from medextract.errors import (
    ModelRefusal,
    OutputTruncated,
    ProviderAuthError,
    ProviderError,
    ProviderUnavailable,
    SchemaParseError,
)
from medextract.providers.base import (
    ContentPart,
    ImagePart,
    PdfPart,
    StructuredRequest,
    StructuredResponse,
    TextPart,
)

log = logging.getLogger(__name__)

# Efforts at which thinking may be turned off (xhigh/max with thinking off is a 400).
_THINKING_OFF_EFFORTS = {None, "low", "medium", "high"}


def thinking_param(model: str, mode: str, effort: str | None) -> dict[str, str] | None:
    """Map our neutral thinking mode to the per-model API value (``None`` = omit the param).

    Adaptive thinking is the default on the 5.5 models, so "adaptive" sends nothing. "off" has a
    different spelling per model, and Opus 5.5 cannot turn thinking off at all.
    """
    if mode == "adaptive":
        return None
    if effort not in _THINKING_OFF_EFFORTS:
        log.warning("thinking=off is not allowed at effort=%s; keeping adaptive thinking", effort)
        return None
    if model.startswith("claude-opus-5-5"):
        log.info("%s cannot disable thinking; omitting the thinking param", model)
        return None
    if model.startswith("claude-sonnet-5-5"):
        return {"type": "between_tools"}  # Sonnet 5.5 rejects "disabled"
    return {"type": "disabled"}


def content_block(part: ContentPart) -> dict[str, Any]:
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    if isinstance(part, ImagePart):
        source = {"type": "base64", "media_type": part.media_type, "data": _b64(part.data)}
        return {"type": "image", "source": source}
    if isinstance(part, PdfPart):
        source = {"type": "base64", "media_type": "application/pdf", "data": _b64(part.data)}
        return {"type": "document", "source": source}
    raise TypeError(f"unsupported content part: {type(part).__name__}")


def _b64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode("ascii")


def build_request_kwargs(req: StructuredRequest[Any], max_tokens: int) -> dict[str, Any]:
    """Keyword arguments for ``client.messages.parse`` (pure, so tests can inspect it)."""
    output_config: dict[str, Any] = {
        "format": {"type": "json_schema", "schema": transform_schema(req.output_model)},
    }
    if req.effort:
        output_config["effort"] = req.effort
    kwargs: dict[str, Any] = {
        "model": req.model,
        "max_tokens": max_tokens,
        # The system prompt is identical across documents of a stage: cache it.
        "system": [{"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": [content_block(p) for p in req.content]}],
        "output_config": output_config,
    }
    thinking = thinking_param(req.model, req.thinking, req.effort)
    if thinking is not None:
        kwargs["thinking"] = thinking
    return kwargs


def map_sdk_error(exc: Exception) -> ProviderError:
    """Translate SDK exceptions into our taxonomy, most specific first."""
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return ProviderAuthError(f"Anthropic auth failed: {_api_message(exc)}")
    if isinstance(exc, anthropic.BadRequestError):
        return ProviderError(f"Anthropic rejected the request: {_api_message(exc)}")
    if isinstance(exc, (anthropic.RateLimitError, anthropic.InternalServerError)):
        return ProviderUnavailable(f"Anthropic unavailable after SDK retries: {_api_message(exc)}")
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return ProviderUnavailable(f"Anthropic unavailable after SDK retries: {_api_message(exc)}")
        return ProviderError(f"Anthropic API error {exc.status_code}: {_api_message(exc)}")
    if isinstance(exc, anthropic.APIConnectionError):  # includes APITimeoutError
        return ProviderUnavailable(f"cannot reach Anthropic after SDK retries: {exc}")
    return ProviderError(f"Anthropic SDK error: {exc}")


def _api_message(exc: anthropic.APIError) -> str:
    """The API's own error text (e.g. 'schema too complex'), not the SDK's wrapped repr."""
    body = exc.body
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
    return exc.message


@dataclass
class _Usage:
    """Token totals summed over every attempt - retries are billed too."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def add(self, usage: Any) -> None:
        if usage is None:
            return
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_input_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_creation_input_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0


def _text_of(message: Any) -> str:
    blocks: Sequence[Any] = getattr(message, "content", None) or []
    return "".join(b.text for b in blocks if getattr(b, "type", None) == "text")


def _validate[T: BaseModel](message: Any, raw_text: str, model: type[T]) -> T | None:
    """The SDK-parsed object if present, else our own validation of the text; None on failure."""
    parsed = getattr(message, "parsed_output", None)
    if isinstance(parsed, model):
        return parsed
    try:
        return model.model_validate_json(raw_text)
    except pydantic.ValidationError as exc:
        log.warning("structured output failed validation: %s", exc.error_count())
        return None


class AnthropicProvider:
    def __init__(
        self,
        settings: Settings,
        pricing: PricingTable | None = None,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self._settings = settings
        self._pricing = pricing or PricingTable.from_settings(settings)
        self._client = client

    @property
    def client(self) -> AsyncAnthropic:
        # Created lazily so a replay-only run never needs ANTHROPIC_API_KEY.
        if self._client is None:
            self._client = AsyncAnthropic(
                timeout=self._settings.timeout_s, max_retries=self._settings.sdk_max_retries
            )
        return self._client

    async def structured[T: BaseModel](self, req: StructuredRequest[T]) -> StructuredResponse[T]:
        usage = _Usage()
        max_tokens = req.max_tokens
        attempts = parse_failures = 0
        truncation_retried = False
        started = time.perf_counter()

        def record(message: Any) -> CallRecord:
            call = CallRecord(
                stage=req.stage,
                model=req.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
                latency_ms=round((time.perf_counter() - started) * 1000),
                stop_reason=getattr(message, "stop_reason", None),
                attempts=attempts,
                request_id=getattr(message, "_request_id", None),
            )
            call.cost_usd = self._pricing.cost(call)
            return call

        while True:
            attempts += 1
            try:
                message = await self._call(req, max_tokens)
            except ProviderError as exc:
                if attempts > 1:  # earlier attempts were billed; keep their cost visible
                    raise _with_call(exc, record(None)) from exc.__cause__
                raise
            usage.add(getattr(message, "usage", None))
            stop = getattr(message, "stop_reason", None)

            if stop == "refusal":
                raise _with_call(ModelRefusal(_refusal_text(message)), record(message))
            if stop == "model_context_window_exceeded":
                raise _with_call(OutputTruncated("input exceeds the model context window"), record(message))
            if stop == "max_tokens":
                grown = min(max_tokens * 2, self._settings.max_tokens_ceiling)
                if truncation_retried or grown <= max_tokens:
                    msg = f"output truncated at max_tokens={max_tokens} ({req.stage})"
                    raise _with_call(OutputTruncated(msg), record(message))
                log.info("%s hit max_tokens=%d; retrying with %d", req.stage, max_tokens, grown)
                truncation_retried, max_tokens = True, grown
                continue

            raw_text = _text_of(message)
            parsed = _validate(message, raw_text, req.output_model)
            if parsed is not None:
                return StructuredResponse(parsed=parsed, call=record(message), raw_text=raw_text)
            parse_failures += 1
            if parse_failures > self._settings.parse_retries:
                msg = f"{req.stage}: output did not match {req.output_model.__name__} after {attempts} attempts"
                raise _with_call(SchemaParseError(msg), record(message))

    async def _call(self, req: StructuredRequest[Any], max_tokens: int) -> Any:
        kwargs = build_request_kwargs(req, max_tokens)
        log.debug(
            "messages.parse stage=%s model=%s max_tokens=%d parts=%s",
            req.stage, req.model, max_tokens, [_describe(p) for p in req.content],
        )  # fmt: skip
        try:
            return await self.client.messages.parse(**kwargs)
        except anthropic.AnthropicError as exc:
            raise map_sdk_error(exc) from exc


def _describe(part: ContentPart) -> str:
    """Loggable summary of a part: sizes only, never the bytes."""
    if isinstance(part, TextPart):
        return f"text[{len(part.text)} chars]"
    if isinstance(part, ImagePart):
        return f"{part.media_type}[{len(part.data)} B]"
    return f"pdf[{len(part.data)} B]"


def _refusal_text(message: Any) -> str:
    details = getattr(message, "stop_details", None)
    category = getattr(details, "category", None)
    explanation = getattr(details, "explanation", None)
    text = "model refused to answer"
    if category:
        text += f" (category: {category})"
    if explanation:
        text += f": {explanation}"
    return text


def _with_call(exc: ProviderError, call: CallRecord) -> ProviderError:
    """Attach the spent CallRecord so callers can still account for the cost of a failed call."""
    exc.call = call  # type: ignore[attr-defined]
    return exc
