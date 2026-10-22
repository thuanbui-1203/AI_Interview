"""Provider layer tests. No network: the Anthropic client is a hand-rolled fake."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from medextract.config import PricingTable, Settings
from medextract.errors import (
    ModelRefusal,
    OutputTruncated,
    ProviderAuthError,
    ProviderError,
    ProviderUnavailable,
    SchemaParseError,
)
from medextract.providers import (
    AnthropicProvider,
    CassetteMiss,
    FakeProvider,
    ImagePart,
    PdfPart,
    RecordReplayProvider,
    StructuredRequest,
    TextPart,
    cassette_key,
)
from medextract.providers.anthropic_provider import thinking_param


class Answer(BaseModel):
    document_type: str
    confidence: float


class AnswerV2(BaseModel):
    document_type: str
    confidence: float
    language: str | None


GOOD = '{"document_type": "receipt", "confidence": 0.9}'
PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"
PDF = b"%PDF-1.7 fake"


# --- fakes ------------------------------------------------------------------------------------
def make_message(
    text: str = GOOD,
    stop_reason: str = "end_turn",
    *,
    input_tokens: int = 1000,
    output_tokens: int = 100,
    cache_read: int | None = None,
    cache_write: int | None = None,
    stop_details: Any = None,
    request_id: str = "req_1",
) -> SimpleNamespace:
    """Shaped like the SDK's ParsedMessage (only the attributes the provider reads)."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
        parsed_output=None,
        _request_id=request_id,
    )


class FakeMessages:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, *outcomes: Any) -> None:
        self.messages = FakeMessages(list(outcomes))


def make_provider(*outcomes: Any, **settings: Any) -> tuple[AnthropicProvider, FakeClient]:
    client = FakeClient(*outcomes)
    provider = AnthropicProvider(Settings(**settings), PricingTable(), client=client)  # type: ignore[arg-type]
    return provider, client


def make_request(**overrides: Any) -> StructuredRequest[Any]:
    base: dict[str, Any] = {
        "stage": "classify",
        "model": "claude-haiku-5-5",
        "system": "You classify medical documents.",
        "content": [TextPart("Classify this."), ImagePart("image/png", PNG)],
        "output_model": Answer,
        "max_tokens": 1024,
        "effort": "low",
        "thinking": "adaptive",
    }
    base.update(overrides)
    return StructuredRequest(**base)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


_REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(cls: type[anthropic.APIStatusError], status: int, message: str) -> anthropic.APIStatusError:
    body = {"type": "error", "error": {"type": "some_error", "message": message}}
    response = httpx2.Response(status, request=_REQ, json=body, headers={"request-id": "req_err"})
    return cls(f"Error code: {status}", response=response, body=body)


# --- request building -------------------------------------------------------------------------
def test_request_shape_system_cache_and_image_block() -> None:
    provider, client = make_provider(make_message())
    run(provider.structured(make_request()))
    kw = client.messages.calls[0]

    assert kw["model"] == "claude-haiku-5-5"
    assert kw["max_tokens"] == 1024
    assert kw["system"] == [
        {"type": "text", "text": "You classify medical documents.", "cache_control": {"type": "ephemeral"}}
    ]
    assert kw["messages"][0]["role"] == "user"
    text, image = kw["messages"][0]["content"]
    assert text == {"type": "text", "text": "Classify this."}
    assert image["type"] == "image"
    assert image["source"]["type"] == "base64"
    assert image["source"]["media_type"] == "image/png"
    assert base64.standard_b64decode(image["source"]["data"]) == PNG
    assert kw["output_config"]["effort"] == "low"
    schema = kw["output_config"]["format"]["schema"]
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert schema["additionalProperties"] is False
    assert "thinking" not in kw  # adaptive is the default: omitted


def test_pdf_part_becomes_document_block() -> None:
    provider, client = make_provider(make_message())
    run(provider.structured(make_request(content=[PdfPart(PDF)])))
    (block,) = client.messages.calls[0]["messages"][0]["content"]
    assert block["type"] == "document"
    assert block["source"]["media_type"] == "application/pdf"
    assert base64.standard_b64decode(block["source"]["data"]) == PDF


def test_no_effort_means_no_effort_key() -> None:
    provider, client = make_provider(make_message())
    run(provider.structured(make_request(effort=None)))
    assert "effort" not in client.messages.calls[0]["output_config"]


@pytest.mark.parametrize(
    ("model", "mode", "effort", "expected"),
    [
        ("claude-sonnet-5-5", "off", "medium", {"type": "between_tools"}),
        ("claude-haiku-5-5", "off", "low", {"type": "disabled"}),
        ("claude-haiku-5-5", "off", None, {"type": "disabled"}),
        ("claude-opus-5-5", "off", "medium", None),  # cannot disable: omitted
        ("claude-sonnet-5-5", "off", "xhigh", None),  # thinking off is a 400 at xhigh/max
        ("claude-haiku-5-5", "off", "max", None),
        ("claude-sonnet-5-5", "adaptive", "high", None),
    ],
)
def test_thinking_mapping(model: str, mode: str, effort: str | None, expected: Any) -> None:
    assert thinking_param(model, mode, effort) == expected


def test_thinking_off_is_sent_for_sonnet() -> None:
    provider, client = make_provider(make_message())
    run(provider.structured(make_request(model="claude-sonnet-5-5", thinking="off", effort="medium")))
    assert client.messages.calls[0]["thinking"] == {"type": "between_tools"}


def test_image_bytes_not_in_repr() -> None:
    assert "fake-image-bytes" not in repr(make_request())


# --- success path: usage, cost, request id ----------------------------------------------------
def test_success_records_usage_and_cost() -> None:
    msg = make_message(input_tokens=2000, output_tokens=300, cache_read=10_000, cache_write=None, request_id="req_x")
    provider, _ = make_provider(msg)
    resp = run(provider.structured(make_request()))

    assert resp.parsed == Answer(document_type="receipt", confidence=0.9)
    assert resp.raw_text == GOOD
    call = resp.call
    assert (call.stage, call.model, call.attempts) == ("classify", "claude-haiku-5-5", 1)
    assert (call.input_tokens, call.output_tokens) == (2000, 300)
    assert (call.cache_read_input_tokens, call.cache_creation_input_tokens) == (10_000, 0)
    assert call.request_id == "req_x"
    assert call.stop_reason == "end_turn"
    assert call.latency_ms >= 0
    assert call.cost_usd == pytest.approx((2000 * 0.10 + 300 * 0.50 + 10_000 * 0.01) / 1e6)


def test_sdk_parsed_output_is_used_when_present() -> None:
    msg = make_message(text="not json")
    msg.parsed_output = Answer(document_type="lab_report", confidence=0.5)
    provider, _ = make_provider(msg)
    assert run(provider.structured(make_request())).parsed.document_type == "lab_report"


# --- stop reasons and retries -----------------------------------------------------------------
def test_refusal_raises_with_category() -> None:
    details = SimpleNamespace(type="refusal", category="bio", explanation=None)
    provider, client = make_provider(make_message(text="", stop_reason="refusal", stop_details=details))
    with pytest.raises(ModelRefusal, match="bio") as info:
        run(provider.structured(make_request()))
    assert len(client.messages.calls) == 1
    assert info.value.call.attempts == 1  # type: ignore[attr-defined]


def test_max_tokens_retries_with_double_budget_then_succeeds() -> None:
    provider, client = make_provider(
        make_message(text='{"document_type": "rec', stop_reason="max_tokens", output_tokens=1024),
        make_message(output_tokens=50),
    )
    resp = run(provider.structured(make_request()))
    assert [c["max_tokens"] for c in client.messages.calls] == [1024, 2048]
    assert resp.call.attempts == 2
    assert resp.call.output_tokens == 1074  # both attempts are billed
    assert resp.call.input_tokens == 2000


def test_max_tokens_respects_ceiling() -> None:
    provider, client = make_provider(
        make_message(stop_reason="max_tokens"), make_message(), max_tokens_ceiling=1500
    )
    run(provider.structured(make_request()))
    assert [c["max_tokens"] for c in client.messages.calls] == [1024, 1500]


def test_max_tokens_twice_raises_truncated() -> None:
    provider, client = make_provider(make_message(stop_reason="max_tokens"), make_message(stop_reason="max_tokens"))
    with pytest.raises(OutputTruncated) as info:
        run(provider.structured(make_request()))
    assert len(client.messages.calls) == 2
    assert info.value.call.attempts == 2  # type: ignore[attr-defined]


def test_max_tokens_at_ceiling_does_not_retry() -> None:
    provider, client = make_provider(make_message(stop_reason="max_tokens"), max_tokens_ceiling=1024)
    with pytest.raises(OutputTruncated):
        run(provider.structured(make_request()))
    assert len(client.messages.calls) == 1


def test_parse_failure_retried_then_succeeds() -> None:
    provider, client = make_provider(make_message(text='{"document_type": 5}'), make_message())
    resp = run(provider.structured(make_request()))
    assert len(client.messages.calls) == 2
    assert resp.call.attempts == 2


def test_parse_failure_exhausts_retries() -> None:
    provider, client = make_provider(make_message(text="nope"), make_message(text="still nope"))
    with pytest.raises(SchemaParseError):
        run(provider.structured(make_request()))
    assert len(client.messages.calls) == 2  # 1 + parse_retries(1)


def test_parse_retries_zero() -> None:
    provider, client = make_provider(make_message(text="nope"), parse_retries=0)
    with pytest.raises(SchemaParseError):
        run(provider.structured(make_request()))
    assert len(client.messages.calls) == 1


# --- SDK exception mapping --------------------------------------------------------------------
@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (status_error(anthropic.AuthenticationError, 401, "invalid x-api-key"), ProviderAuthError),
        (status_error(anthropic.PermissionDeniedError, 403, "forbidden"), ProviderAuthError),
        (status_error(anthropic.RateLimitError, 429, "slow down"), ProviderUnavailable),
        (status_error(anthropic.InternalServerError, 500, "boom"), ProviderUnavailable),
        (status_error(anthropic.OverloadedError, 529, "overloaded"), ProviderUnavailable),
        (status_error(anthropic.APIStatusError, 502, "bad gateway"), ProviderUnavailable),
        (status_error(anthropic.NotFoundError, 404, "model not found"), ProviderError),
        (anthropic.APIConnectionError(request=_REQ), ProviderUnavailable),
        (anthropic.APITimeoutError(request=_REQ), ProviderUnavailable),
    ],
)
def test_sdk_exception_mapping(exc: Exception, expected: type[Exception]) -> None:
    provider, _ = make_provider(exc)
    with pytest.raises(expected) as info:
        run(provider.structured(make_request()))
    assert info.value.__cause__ is exc


def test_bad_request_carries_api_message() -> None:
    exc = status_error(anthropic.BadRequestError, 400, "Schema is too complex for compilation")
    provider, _ = make_provider(exc)
    with pytest.raises(ProviderError, match="Schema is too complex") as info:
        run(provider.structured(make_request()))
    assert type(info.value) is ProviderError


# --- FakeProvider -----------------------------------------------------------------------------
def test_fake_provider_queue_and_requests() -> None:
    fake = FakeProvider(
        {"classify": [{"document_type": "receipt", "confidence": 0.8}], "extract": [ModelRefusal("no")]}
    )
    resp = run(fake.structured(make_request()))
    assert resp.parsed == Answer(document_type="receipt", confidence=0.8)
    assert resp.call.stage == "classify"
    with pytest.raises(ModelRefusal):
        run(fake.structured(make_request(stage="extract")))
    assert [r.stage for r in fake.requests] == ["classify", "extract"]
    with pytest.raises(AssertionError, match="no canned response"):
        run(fake.structured(make_request()))


def test_fake_provider_callable() -> None:
    fake = FakeProvider(lambda req: Answer(document_type=req.model, confidence=1.0))
    assert run(fake.structured(make_request())).parsed.document_type == "claude-haiku-5-5"


# --- cassettes --------------------------------------------------------------------------------
def test_cassette_record_then_replay(tmp_path: Path) -> None:
    fake = FakeProvider({"classify": [{"document_type": "hóa đơn", "confidence": 0.7}]})
    recorder = RecordReplayProvider(fake, tmp_path, "record")
    recorded = run(recorder.structured(make_request()))

    (path,) = tmp_path.iterdir()
    assert path.name == f"classify-{cassette_key(make_request())[:16]}.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert "fake-image-bytes" not in path.read_text(encoding="utf-8")
    assert stored["key_inputs"]["content"][1]["type"] == "image"
    assert "sha256" in stored["key_inputs"]["content"][1]

    replayer = RecordReplayProvider(None, tmp_path, "replay")
    replayed = run(replayer.structured(make_request()))
    assert replayed.parsed == recorded.parsed
    assert replayed.call == recorded.call
    assert replayed.raw_text == recorded.raw_text
    assert len(fake.requests) == 1  # replay never calls the inner provider


def test_cassette_replay_miss(tmp_path: Path) -> None:
    replayer = RecordReplayProvider(None, tmp_path, "replay")
    with pytest.raises(CassetteMiss, match="no cassette") as info:
        run(replayer.structured(make_request()))
    assert isinstance(info.value, ProviderError)


def test_cassette_off_is_passthrough(tmp_path: Path) -> None:
    fake = FakeProvider({"classify": [{"document_type": "x", "confidence": 0.1}]})
    run(RecordReplayProvider(fake, tmp_path, "off").structured(make_request()))
    assert list(tmp_path.iterdir()) == []


def test_cassette_key_stability() -> None:
    key = cassette_key(make_request())
    assert key == cassette_key(make_request())  # same inputs, same key
    assert key != cassette_key(make_request(output_model=AnswerV2))  # schema change
    assert key != cassette_key(make_request(system="You classify medical documents!"))  # prompt change
    assert key != cassette_key(make_request(content=[TextPart("Classify this."), ImagePart("image/png", PNG + b"!")]))
    assert key != cassette_key(make_request(effort="high"))
    assert key != cassette_key(make_request(model="claude-sonnet-5-5"))


# --- real SDK client over a mock transport (no network) --------------------------------------
def test_real_sdk_client_round_trip() -> None:
    """Proves the kwargs we build are accepted by the installed SDK and its ParsedMessage reads."""
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        body = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "content": [{"type": "text", "text": GOOD}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 7},
        }
        return httpx2.Response(200, json=body, headers={"request-id": "req_mock"})

    http = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    client = anthropic.AsyncAnthropic(api_key="test-key", http_client=http, max_retries=0)
    provider = AnthropicProvider(Settings(), PricingTable(), client=client)
    resp = run(provider.structured(make_request(model="claude-sonnet-5-5", thinking="off")))

    assert resp.parsed == Answer(document_type="receipt", confidence=0.9)
    assert resp.call.request_id == "req_mock"
    assert resp.call.cache_read_input_tokens == 7
    assert resp.call.cache_creation_input_tokens == 0
    body = seen["body"]
    assert body["thinking"] == {"type": "between_tools"}
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"][0]["content"][1]["source"]["media_type"] == "image/png"


# --- review regressions -----------------------------------------------------------------------
def test_sdk_error_after_retry_keeps_cost_of_earlier_attempts() -> None:
    provider, _ = make_provider(
        make_message(stop_reason="max_tokens"), status_error(anthropic.InternalServerError, 500, "boom")
    )
    with pytest.raises(ProviderUnavailable) as info:
        run(provider.structured(make_request()))
    call = info.value.call  # type: ignore[attr-defined]
    assert call.attempts == 2 and call.input_tokens == 1000 and call.cost_usd > 0


def test_stage_with_path_chars_stays_in_cassette_dir(tmp_path: Path) -> None:
    rec = RecordReplayProvider(FakeProvider(lambda r: Answer(document_type="x", confidence=1)), tmp_path, "record")
    run(rec.structured(make_request(stage="extract/receipt:v1")))
    (path,) = tmp_path.iterdir()
    assert path.is_file() and path.name.startswith("extract_receipt_v1-")


def test_corrupt_cassette_is_a_provider_error(tmp_path: Path) -> None:
    replayer = RecordReplayProvider(None, tmp_path, "replay")
    replayer.path_for(make_request()).write_text("{not json", encoding="utf-8")
    with pytest.raises(CassetteMiss, match="unreadable cassette"):
        run(replayer.structured(make_request()))
