"""Gemini adapter (google-genai SDK): same contract as the Claude adapter.

Free-tier specifics drive most of this file:

- Requests-per-minute quotas are small, so requests are spaced client-side (``gemini_rpm``) and a
  429 is retried after the delay the API asks for (``RetryInfo.retryDelay``). A *daily* quota
  error is not retried - waiting minutes cannot fix it.
- Retry ownership is ours: the SDK's own retry is disabled so attempts and cost stay visible.

Other differences from Claude that matter for correctness:

- Thinking tokens count against ``max_output_tokens`` and are billed as output.
- The JSON schema is sent with ``$defs`` inlined; our schemas are non-recursive (schema_lint), so
  inlining is always possible and avoids depending on ``$ref`` support in the schema subset.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import re
import time
from typing import Any

import pydantic
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
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
from medextract.providers.base import ContentPart, ImagePart, PdfPart, StructuredRequest, StructuredResponse, TextPart

log = logging.getLogger(__name__)

# Finish reasons that mean "the model would not answer", as opposed to a truncated answer.
_BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY", "LANGUAGE", "OTHER"}
_EFFORT_TO_LEVEL = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH", "xhigh": "HIGH", "max": "HIGH"}
_EFFORT_TO_BUDGET = {"low": 1024, "medium": 4096, "high": 8192, "xhigh": 16384, "max": 24576}
SERVER_ERROR_BACKOFF_S = 20.0  # 503 "high demand" clears in tens of seconds, not in 1-2 s
MAX_RETRY_WAIT_S = 90.0  # a longer requested wait means a daily quota: give up instead


def inline_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema with every ``$ref`` replaced by its definition."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                target = copy.deepcopy(defs[node["$ref"].removeprefix("#/$defs/")])
                extra = {k: v for k, v in node.items() if k != "$ref"}  # e.g. a field description
                return resolve({**target, **extra})
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


def thinking_config(model: str, mode: str, effort: str | None) -> types.ThinkingConfig | None:
    """Gemini 3+ takes a thinking *level*; 2.x takes a token *budget* (0 = off, flash models only)."""
    if re.match(r"gemini-[3-9]", model):
        if mode == "off":
            return types.ThinkingConfig(thinking_level="MINIMAL")
        return types.ThinkingConfig(thinking_level=_EFFORT_TO_LEVEL[effort]) if effort else None
    if mode == "off":
        return types.ThinkingConfig(thinking_budget=0)
    return types.ThinkingConfig(thinking_budget=_EFFORT_TO_BUDGET[effort]) if effort else None


def to_part(part: ContentPart) -> types.Part:
    if isinstance(part, TextPart):
        return types.Part.from_text(text=part.text)
    if isinstance(part, ImagePart):
        return types.Part.from_bytes(data=part.data, mime_type=part.media_type)
    if isinstance(part, PdfPart):
        return types.Part.from_bytes(data=part.data, mime_type="application/pdf")
    raise TypeError(f"unsupported content part: {type(part).__name__}")


def build_config(req: StructuredRequest[Any], max_tokens: int) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=req.system,
        max_output_tokens=max_tokens,
        response_mime_type="application/json",
        response_json_schema=inline_schema(req.output_model),
        thinking_config=thinking_config(req.model, req.thinking, req.effort),
    )


def retry_delay_s(exc: genai_errors.APIError) -> float | None:
    """The wait the API asks for on a 429 ('37s' in RetryInfo), if any."""
    for detail in _error_details(exc):
        if str(detail.get("@type", "")).endswith("RetryInfo") and (m := re.match(r"([\d.]+)s", str(detail.get("retryDelay", "")))):
            return float(m.group(1))
    return None


def is_daily_quota(exc: genai_errors.APIError) -> bool:
    text = str(_error_details(exc))
    return "PerDay" in text or "per day" in text.lower()


def _error_details(exc: genai_errors.APIError) -> list[dict[str, Any]]:
    body = exc.details if isinstance(exc.details, dict) else {}
    error = body.get("error", body)
    details = error.get("details", []) if isinstance(error, dict) else []
    return [d for d in details if isinstance(d, dict)]


def map_api_error(exc: genai_errors.APIError) -> ProviderError:
    msg = exc.message or str(exc)
    if exc.code in (401, 403):
        return ProviderAuthError(f"Gemini auth failed ({exc.code}): {msg}")
    if exc.code == 429:
        kind = "daily quota exhausted" if is_daily_quota(exc) else "rate limited"
        return ProviderUnavailable(f"Gemini {kind}: {msg}")
    if exc.code >= 500:
        return ProviderUnavailable(f"Gemini unavailable ({exc.code}): {msg}")
    return ProviderError(f"Gemini rejected the request ({exc.code}): {msg}")


class _RequestSpacer:
    """Client-side requests-per-minute limiter: starts are spaced 60/rpm seconds apart."""

    def __init__(self, rpm: int) -> None:
        self._interval = 60.0 / rpm if rpm > 0 else 0.0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if not self._interval:
            return
        async with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self._interval
        if delay > 0:
            await asyncio.sleep(delay)


class GeminiProvider:
    def __init__(self, settings: Settings, pricing: PricingTable | None = None, client: Any | None = None) -> None:
        self._settings = settings
        self._pricing = pricing or PricingTable.from_settings(settings)
        self._client = client
        self._spacer = _RequestSpacer(settings.gemini_rpm)

    @property
    def client(self) -> Any:
        # Lazy: a replay-only run never needs GEMINI_API_KEY.
        if self._client is None:
            secret = self._settings.gemini_api_key
            key = secret.get_secret_value() if secret else os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not key:
                raise ProviderAuthError("GEMINI_API_KEY is not set (get a free key at https://aistudio.google.com/apikey)")
            self._client = genai.Client(
                api_key=key,
                http_options=types.HttpOptions(
                    timeout=int(self._settings.timeout_s * 1000),  # milliseconds in google-genai
                    retry_options=types.HttpRetryOptions(attempts=1),  # retries are ours (see module doc)
                ),
            )
        return self._client

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
                request_id=getattr(last, "response_id", None),
            )
            call.cost_usd = self._pricing.cost(call)
            return call

        while True:
            attempts += 1
            await self._spacer.wait()
            try:
                last = await self.client.aio.models.generate_content(
                    model=req.model, contents=[to_part(p) for p in req.content], config=build_config(req, max_tokens)
                )
            except genai_errors.APIError as exc:
                # A rejected request still counts against the free-tier daily quota (observed: 8 of 21
                # attempts were 503 "high demand" retries), so overload is retried rarely and slowly.
                if exc.code >= 500:
                    wait = SERVER_ERROR_BACKOFF_S * 2**server_retries
                    budget_left = server_retries < self._settings.gemini_server_error_retries
                else:
                    wait = retry_delay_s(exc) if exc.code == 429 else None
                    budget_left = rate_retries < self._settings.gemini_rate_limit_retries
                retryable = wait is not None and wait <= MAX_RETRY_WAIT_S and not is_daily_quota(exc)
                if retryable and budget_left:
                    if exc.code >= 500:
                        server_retries += 1
                    else:
                        rate_retries += 1
                    retry_reasons.append(str(exc.code))
                    log.info("%s: Gemini %s, retrying in %.0fs", req.stage, exc.code, wait)
                    await asyncio.sleep(wait or 0)
                    continue
                raise _with_call(map_api_error(exc), record()) from exc
            _add_usage(tokens, getattr(last, "usage_metadata", None))

            if (block := _prompt_block_reason(last)) is not None:
                raise _with_call(ModelRefusal(f"Gemini blocked the prompt: {block}"), record())
            reason = _finish_reason(last)
            if reason in _BLOCKED:
                raise _with_call(ModelRefusal(f"Gemini stopped with finish_reason={reason}"), record())
            if reason == "MAX_TOKENS":
                grown = min(max_tokens * 2, self._settings.max_tokens_ceiling)
                if truncation_retried or grown <= max_tokens:
                    raise _with_call(OutputTruncated(f"output truncated at max_output_tokens={max_tokens} ({req.stage})"), record())
                truncation_retried, max_tokens = True, grown
                retry_reasons.append("MAX_TOKENS")
                continue

            raw_text = _answer_text(last)
            try:
                parsed = req.output_model.model_validate_json(raw_text)
                return StructuredResponse(parsed=parsed, call=record(), raw_text=raw_text)
            except pydantic.ValidationError as exc:
                log.warning("%s: Gemini output failed validation (%d errors)", req.stage, exc.error_count())
                parse_failures += 1
                retry_reasons.append("parse")
                if parse_failures > self._settings.parse_retries:
                    msg = f"{req.stage}: output did not match {req.output_model.__name__} after {attempts} attempts"
                    raise _with_call(SchemaParseError(msg), record()) from exc


def _add_usage(tokens: dict[str, int], usage: Any) -> None:
    """prompt_token_count includes cached tokens; thinking tokens are billed as output."""
    if usage is None:
        return
    cached = getattr(usage, "cached_content_token_count", 0) or 0
    tokens["cached"] += cached
    tokens["input"] += max(0, (getattr(usage, "prompt_token_count", 0) or 0) - cached)
    tokens["output"] += (getattr(usage, "candidates_token_count", 0) or 0) + (getattr(usage, "thoughts_token_count", 0) or 0)


def _finish_reason(resp: Any) -> str | None:
    candidates = getattr(resp, "candidates", None) or []
    reason = getattr(candidates[0], "finish_reason", None) if candidates else None
    return getattr(reason, "name", reason)


def _prompt_block_reason(resp: Any) -> str | None:
    feedback = getattr(resp, "prompt_feedback", None)
    reason = getattr(feedback, "block_reason", None)
    return getattr(reason, "name", reason) if reason else None


def _answer_text(resp: Any) -> str:
    """Concatenated non-thought text parts of the first candidate."""
    candidates = getattr(resp, "candidates", None) or []
    content = getattr(candidates[0], "content", None) if candidates else None
    parts = getattr(content, "parts", None) or []
    return "".join(p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False))


def _with_call(exc: ProviderError, call: CallRecord) -> ProviderError:
    exc.call = call  # type: ignore[attr-defined]
    return exc
