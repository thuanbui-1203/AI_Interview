import asyncio
import base64
import io
import json

import groq
import httpx
import pytest
from groq.types.chat import ChatCompletion
from PIL import Image

from medextract.config import PricingTable, Settings
from medextract.core.fields import LLMModel, TextField
from medextract.errors import (
    OutputTruncated,
    ProviderAuthError,
    ProviderError,
    ProviderUnavailable,
    SchemaParseError,
    TooManyPages,
)
from medextract.providers import groq_provider as Q
from medextract.providers.base import ImagePart, PdfPart, StructuredRequest, TextPart


class Out(LLMModel):
    name: TextField


GOOD = {"name": {"evidence": "Name: An", "page": 1, "status": "present", "value": "An", "confidence": "certain"}}


def png(w=3000, h=2000) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "PNG")
    return buf.getvalue()


def completion(content: str | None, finish="stop", prompt=1000, completion_tokens=300, cached=0) -> ChatCompletion:
    return ChatCompletion.model_validate({
        "id": "chatcmpl-1", "object": "chat.completion", "created": 0, "model": "qwen/qwen3.8-27b",
        "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion_tokens, "total_tokens": prompt + completion_tokens,
                  "prompt_tokens_details": {"cached_tokens": cached}},
    })  # fmt: skip


def status_error(cls, code: int, message: str = "boom", retry_after: str | None = None):
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    headers = {"retry-after": retry_after} if retry_after else {}
    return cls(message, response=httpx.Response(code, request=req, headers=headers), body={"error": {"message": message}})


class FakeClient:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []
        self.chat = self
        self.completions = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(round(s, 2))

    monkeypatch.setattr(Q.asyncio, "sleep", fake_sleep)
    return slept


def provider(*outcomes, **settings):
    settings.setdefault("groq_output_tpm", 0)  # pacing is tested separately
    s = Settings(_env_file=None, provider="groq", **settings)
    client = FakeClient(*outcomes)
    return Q.GroqProvider(s, PricingTable.from_settings(s), client=client), client


def request(images=1, effort="medium", thinking="adaptive", content=None):
    parts = content or [TextPart("Page 1 of 1"), *[ImagePart("image/png", png()) for _ in range(images)]]
    return StructuredRequest(stage="extract", model="qwen/qwen3.8-27b", system="SYSTEM", content=parts,
                             output_model=Out, max_tokens=1000, effort=effort, thinking=thinking)  # fmt: skip


def run(p, req):
    return asyncio.run(p.structured(req))


def test_success_builds_strict_schema_request_and_accounts_usage():
    p, client = provider(completion(json.dumps(GOOD), prompt=1000, completion_tokens=300, cached=200))
    res = run(p, request())
    assert res.parsed.name.value == "An"
    kw = client.calls[0]
    assert kw["model"] == "qwen/qwen3.8-27b" and kw["max_completion_tokens"] == 1000
    assert kw["messages"][0] == {"role": "system", "content": "SYSTEM"}
    rf = kw["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True and rf["json_schema"]["name"] == "Out"
    assert rf["json_schema"]["schema"]["additionalProperties"] is False
    assert kw["reasoning_format"] == "hidden" and kw["reasoning_effort"] == "medium"
    c = res.call
    assert (c.input_tokens, c.output_tokens, c.cache_read_input_tokens) == (800, 300, 200)
    assert c.request_id == "chatcmpl-1" and c.stop_reason == "stop" and c.cost_usd == 0.0


def test_images_are_reencoded_as_small_jpeg_data_urls():
    p, client = provider(completion(json.dumps(GOOD)))
    run(p, request())
    part = client.calls[0]["messages"][1]["content"][1]
    url = part["image_url"]["url"]
    assert part["type"] == "image_url" and url.startswith("data:image/jpeg;base64,")
    img = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert img.format == "JPEG" and max(img.size) == 1600


def test_more_images_than_groq_accepts_is_too_many_pages():
    p, client = provider()
    with pytest.raises(TooManyPages, match="at most 3"):
        run(p, request(images=4))
    assert client.calls == []  # rejected before any request is spent


def test_pdf_block_mode_is_not_supported():
    p, _ = provider()
    with pytest.raises(ProviderError, match="raster"):
        run(p, request(content=[PdfPart(b"%PDF-1.7")]))


@pytest.mark.parametrize(
    ("thinking", "effort", "expected"),
    [("off", "high", "none"), ("adaptive", "low", "low"), ("adaptive", "max", "high"), ("adaptive", None, None)],
)
def test_reasoning_effort_mapping(thinking, effort, expected):
    assert Q.reasoning_effort(thinking, effort) == expected


def test_no_effort_omits_the_parameter():
    p, client = provider(completion(json.dumps(GOOD)))
    run(p, request(effort=None))
    assert "reasoning_effort" not in client.calls[0]


def test_length_retried_once_with_double_budget():
    p, client = provider(completion(None, finish="length"), completion(json.dumps(GOOD)))
    res = run(p, request())
    assert [c["max_completion_tokens"] for c in client.calls] == [1000, 2000]
    assert res.call.retry_reasons == ["length"] and res.call.attempts == 2


def test_length_twice_is_truncation():
    p, _ = provider(completion(None, finish="length"), completion(None, finish="length"))
    with pytest.raises(OutputTruncated):
        run(p, request())


def test_empty_or_invalid_json_retried_then_schema_error():
    p, _ = provider(completion(None), completion("{oops"))
    with pytest.raises(SchemaParseError) as e:
        run(p, request())
    assert e.value.call.retry_reasons == ["parse", "parse"]
    p, _ = provider(completion(""), completion(json.dumps(GOOD)))
    assert run(p, request()).call.attempts == 2


@pytest.mark.parametrize(
    ("exc", "expected", "match"),
    [
        (status_error(groq.AuthenticationError, 401), ProviderAuthError, "auth"),
        (status_error(groq.BadRequestError, 400, "json_schema not supported with images"), ProviderError, "not supported"),
        (status_error(groq.RateLimitError, 429, "Rate limit reached on tokens per day (TPD)"), ProviderUnavailable, "daily quota"),
    ],
)
def test_final_errors_map_to_taxonomy(exc, expected, match):
    p, _ = provider(exc)
    with pytest.raises(expected, match=match):
        run(p, request())


def test_missing_key_is_auth_error(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    p = Q.GroqProvider(Settings(_env_file=None, provider="groq"))
    with pytest.raises(ProviderAuthError, match=r"console.groq.com"):
        _ = p.client


def test_settings_switch_default_models_for_groq():
    s = Settings(_env_file=None, provider="groq")
    assert s.classify_model == s.extract_model == "qwen/qwen3.8-27b"


OTPM = "Request too large for model `qwen/qwen3.8-27b` on output tokens per minute (OTPM): Limit 1000, Requested 2605"


def test_per_minute_429_waits_for_retry_after_then_succeeds(no_sleep):
    p, client = provider(status_error(groq.RateLimitError, 429, OTPM, retry_after="42"), completion(json.dumps(GOOD)))
    res = run(p, request())
    assert no_sleep == [42.0] and res.call.retry_reasons == ["429"] and len(client.calls) == 2


def test_per_minute_429_retries_are_bounded(no_sleep):
    p, client = provider(*[status_error(groq.RateLimitError, 429, OTPM)] * 5, groq_rate_limit_retries=2)
    with pytest.raises(ProviderUnavailable, match="rate limited"):
        run(p, request())
    assert no_sleep == [30.0, 30.0] and len(client.calls) == 3  # no retry-after header -> 30 s default


def test_daily_limit_is_final(no_sleep):
    p, client = provider(status_error(groq.RateLimitError, 429, "Rate limit reached on requests per day (RPD)"))
    with pytest.raises(ProviderUnavailable, match="daily quota"):
        run(p, request())
    assert no_sleep == [] and len(client.calls) == 1


@pytest.mark.parametrize(
    "exc",
    [status_error(groq.InternalServerError, 503), groq.APIConnectionError(request=httpx.Request("POST", "https://x"))],
)
def test_transient_errors_back_off_then_give_up(exc, no_sleep):
    p, client = provider(exc, exc, exc, exc, sdk_max_retries=2)
    with pytest.raises(ProviderUnavailable):
        run(p, request())
    assert no_sleep == [5.0, 10.0] and len(client.calls) == 3


def test_output_token_budget_paces_the_next_call(monkeypatch, no_sleep):
    t = [1000.0]
    monkeypatch.setattr(Q.time, "monotonic", lambda: t[0])
    p, _ = provider(completion(json.dumps(GOOD), completion_tokens=2000), completion(json.dumps(GOOD), completion_tokens=500),
                    groq_output_tpm=1000)  # fmt: skip
    run(p, request())  # first call: no debt yet
    assert no_sleep == []
    run(p, request())  # 2000 output tokens at 1000/min -> wait 120 s
    assert no_sleep == [120.0]
