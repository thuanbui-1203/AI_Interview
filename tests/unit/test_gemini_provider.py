import asyncio
import json

import pytest
from google.genai import errors as genai_errors
from google.genai import types

from medextract.config import PricingTable, Settings
from medextract.core.fields import LLMModel, TextField
from medextract.doctypes.receipt import ReceiptExtraction
from medextract.errors import (
    ModelRefusal,
    OutputTruncated,
    ProviderAuthError,
    ProviderError,
    ProviderUnavailable,
    SchemaParseError,
)
from medextract.providers import gemini_provider as G
from medextract.providers.base import ImagePart, PdfPart, StructuredRequest, TextPart


class Out(LLMModel):
    name: TextField


GOOD = {"name": {"evidence": "Name: An", "page": 1, "status": "present", "value": "An", "confidence": "certain"}}


def response(text: str | None = None, finish: str = "STOP", prompt=1000, out=200, thoughts=50, cached=0, block=None, thought_part=False):
    parts = []
    if thought_part:
        parts.append(types.Part(text="let me think...", thought=True))
    if text is not None:
        parts.append(types.Part(text=text))
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts), finish_reason=finish)],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt, candidates_token_count=out, thoughts_token_count=thoughts, cached_content_token_count=cached
        ),
        prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason=block) if block else None,
        response_id="resp-1",
    )


def api_error(code: int, retry: str | None = None, daily: bool = False):
    details = []
    if retry:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry})
    if daily:
        details.append({"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]})
    body = {"error": {"code": code, "message": "boom", "status": "X", "details": details}}
    cls = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return cls(code, body)


class FakeClient:
    """Mimics client.aio.models.generate_content and records every call."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.aio = self
        self.models = self

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


def provider(*outcomes, **settings):
    s = Settings(provider="gemini", gemini_rpm=0, **settings)
    client = FakeClient(*outcomes)
    return G.GeminiProvider(s, PricingTable.from_settings(s), client=client), client


def request(model="gemini-2.5-flash", out=Out, max_tokens=1000, effort="medium", thinking="adaptive"):
    return StructuredRequest(
        stage="extract",
        model=model,
        system="SYSTEM",
        content=[TextPart("Page 1 of 1"), ImagePart("image/png", b"\x89PNG"), PdfPart(b"%PDF-1.7")],
        output_model=out,
        max_tokens=max_tokens,
        effort=effort,
        thinking=thinking,
    )


def run(p, req):
    return asyncio.run(p.structured(req))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(G.asyncio, "sleep", fake_sleep)
    return slept


def test_success_builds_request_and_accounts_usage():
    p, client = provider(response(json.dumps(GOOD), prompt=1000, out=200, thoughts=50, cached=400))
    res = run(p, request())
    assert res.parsed.name.value == "An"
    call = client.calls[0]
    cfg = call["config"]
    assert cfg.system_instruction == "SYSTEM" and cfg.response_mime_type == "application/json"
    assert cfg.max_output_tokens == 1000
    assert [p.text for p in call["contents"][:1]] == ["Page 1 of 1"]
    assert call["contents"][1].inline_data.mime_type == "image/png"
    assert call["contents"][2].inline_data.mime_type == "application/pdf"
    rec = res.call
    assert (rec.input_tokens, rec.output_tokens, rec.cache_read_input_tokens) == (600, 250, 400)
    assert rec.cost_usd == 0.0  # free tier: no Gemini prices configured
    assert rec.request_id == "resp-1" and rec.stop_reason == "STOP" and rec.attempts == 1


def test_price_overrides_give_cost_estimates():
    s = Settings(provider="gemini", gemini_rpm=0, price_overrides={"gemini-2.5-flash": {"input": 0.30, "output": 2.50, "cache_write": 0.30, "cache_read": 0.03}})
    p = G.GeminiProvider(s, client=FakeClient(response(json.dumps(GOOD), prompt=1000, out=200, thoughts=50, cached=400)))
    assert run(p, request()).call.cost_usd == pytest.approx((600 * 0.30 + 250 * 2.50 + 400 * 0.03) / 1e6)


def test_thought_parts_are_not_part_of_the_answer():
    p, _ = provider(response(json.dumps(GOOD), thought_part=True))
    assert run(p, request()).raw_text == json.dumps(GOOD)


def test_schema_is_inlined_without_refs():
    schema = G.inline_schema(ReceiptExtraction)
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    assert schema["properties"]["items"]["properties"]["rows"]["items"]["properties"]["total"]
    # a field-level description survives inlining of the referenced type
    assert "BEFORE deducting insurance" in schema["properties"]["grand_total"]["description"]


@pytest.mark.parametrize(
    ("model", "mode", "effort", "expected"),
    [
        ("gemini-2.5-flash", "off", None, {"thinking_budget": 0}),
        ("gemini-2.5-flash", "adaptive", "low", {"thinking_budget": 1024}),
        ("gemini-2.5-flash", "adaptive", None, None),
        ("gemini-3-flash", "adaptive", "high", {"thinking_level": "HIGH"}),
        ("gemini-3-pro", "off", "low", {"thinking_level": "MINIMAL"}),
    ],
)
def test_thinking_mapping(model, mode, effort, expected):
    cfg = G.thinking_config(model, mode, effort)
    got = None if cfg is None else {k: (v.name if hasattr(v, "name") else v) for k, v in cfg.model_dump(exclude_none=True).items()}
    assert got == expected


def test_max_tokens_retried_once_with_double_budget():
    p, client = provider(response(None, finish="MAX_TOKENS"), response(json.dumps(GOOD)))
    res = run(p, request(max_tokens=1000))
    assert [c["config"].max_output_tokens for c in client.calls] == [1000, 2000]
    assert res.call.attempts == 2 and res.call.output_tokens == 500  # both attempts billed


def test_max_tokens_twice_is_truncation_error_with_cost():
    p, _ = provider(response(None, finish="MAX_TOKENS"), response(None, finish="MAX_TOKENS"))
    with pytest.raises(OutputTruncated) as e:
        run(p, request())
    assert e.value.call.attempts == 2


@pytest.mark.parametrize("finish", ["SAFETY", "PROHIBITED_CONTENT", "RECITATION"])
def test_blocked_finish_is_refusal(finish):
    p, _ = provider(response(None, finish=finish))
    with pytest.raises(ModelRefusal):
        run(p, request())


def test_prompt_block_is_refusal():
    p, _ = provider(response(None, block="SAFETY"))
    with pytest.raises(ModelRefusal, match="SAFETY"):
        run(p, request())


def test_invalid_json_retried_then_schema_error():
    p, _ = provider(response("{not json"), response('{"name": 1}'))
    with pytest.raises(SchemaParseError):
        run(p, request())
    p, _ = provider(response("{not json"), response(json.dumps(GOOD)))
    assert run(p, request()).call.attempts == 2


def test_rate_limit_waits_for_retry_delay_then_succeeds(no_sleep):
    p, _ = provider(api_error(429, "12s"), api_error(503), response(json.dumps(GOOD)))
    res = run(p, request())
    assert res.parsed.name.value == "An" and no_sleep == [12.0, 2.0]
    assert res.call.retry_reasons == ["429", "503"] and res.call.attempts == 3


def test_daily_quota_is_not_retried():
    p, client = provider(api_error(429, "30s", daily=True))
    with pytest.raises(ProviderUnavailable, match="daily quota"):
        run(p, request())
    assert len(client.calls) == 1


def test_too_long_retry_delay_gives_up():
    p, client = provider(api_error(429, "3600s"))
    with pytest.raises(ProviderUnavailable):
        run(p, request())
    assert len(client.calls) == 1


def test_rate_limit_retries_are_bounded():
    p, client = provider(*[api_error(429, "1s")] * 10, gemini_rate_limit_retries=3)
    with pytest.raises(ProviderUnavailable):
        run(p, request())
    assert len(client.calls) == 4


@pytest.mark.parametrize(("code", "exc"), [(400, ProviderError), (401, ProviderAuthError), (403, ProviderAuthError)])
def test_client_errors_map(code, exc):
    p, _ = provider(api_error(code))
    with pytest.raises(exc):
        run(p, request())


def test_missing_key_is_auth_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    p = G.GeminiProvider(Settings(provider="gemini", _env_file=None))  # ignore the developer's real .env
    with pytest.raises(ProviderAuthError, match="aistudio"):
        _ = p.client


def test_request_spacer_spaces_requests(monkeypatch, no_sleep):
    spacer = G._RequestSpacer(rpm=30)  # 2 s apart
    t = [100.0]
    monkeypatch.setattr(G.time, "monotonic", lambda: t[0])

    async def go():
        for _ in range(3):
            await spacer.wait()

    asyncio.run(go())
    assert no_sleep == [2.0, 4.0]


def test_settings_switch_default_models_for_gemini():
    s = Settings(provider="gemini")
    assert s.classify_model.startswith("gemini") and s.extract_model.startswith("gemini")
    assert Settings(provider="gemini", extract_model="custom").extract_model == "custom"
