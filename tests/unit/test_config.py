from __future__ import annotations

import pytest

from medextract.config import ModelPrice, PricingTable, Settings
from medextract.core.models import CallRecord


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # A developer's .env must not leak into the defaults under test.
    monkeypatch.chdir(tmp_path)


def test_defaults() -> None:
    s = Settings()
    assert s.classify_model == "claude-haiku-5-5"
    assert s.extract_model == "claude-sonnet-5-5"
    assert s.strategy == "two_stage"
    assert (s.classify_effort, s.extract_effort, s.thinking) == ("low", "medium", "adaptive")
    assert s.record_mode == "off"
    assert not any("key" in name.lower() for name in Settings.model_fields)


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDX_EXTRACT_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("MEDX_MAX_PAGES", "3")
    monkeypatch.setenv("MEDX_CLASSIFY_EFFORT", "high")
    s = Settings()
    assert s.extract_model == "claude-opus-5-5"
    assert s.max_pages == 3
    assert s.classify_effort == "high"


def test_env_file_is_read(tmp_path) -> None:
    (tmp_path / ".env").write_text("MEDX_STRATEGY=single_call\nUNRELATED=1\n", encoding="utf-8")
    assert Settings().strategy == "single_call"


def test_invalid_literal_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDX_THINKING", "sometimes")
    with pytest.raises(ValueError):
        Settings()


def test_cost_includes_cache_tokens() -> None:
    call = CallRecord(
        stage="extract",
        model="claude-sonnet-5-5",
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_creation_input_tokens=200_000,
        cache_read_input_tokens=500_000,
    )
    # 2.00 + 1.00 + 0.50 + 0.10
    assert PricingTable().cost(call) == pytest.approx(3.60)


def test_cost_haiku_small_call() -> None:
    call = CallRecord(stage="classify", model="claude-haiku-5-5", input_tokens=2000, output_tokens=200)
    assert PricingTable().cost(call) == pytest.approx((2000 * 0.10 + 200 * 0.50) / 1e6)


def test_dated_snapshot_id_uses_base_price() -> None:
    call = CallRecord(stage="x", model="claude-opus-5-5-20261001", input_tokens=1_000_000)
    assert PricingTable().cost(call) == pytest.approx(4.0)


def test_unknown_model_costs_zero_and_is_flagged() -> None:
    table = PricingTable()
    assert table.cost(CallRecord(stage="x", model="gpt-oops", input_tokens=10)) == 0.0
    assert table.unknown_models == {"gpt-oops"}


def test_price_override_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "MEDX_PRICE_OVERRIDES",
        '{"claude-haiku-5-5": {"input": 1, "output": 1, "cache_write": 1, "cache_read": 1}}',
    )
    table = PricingTable.from_settings(Settings())
    assert table.price_for("claude-haiku-5-5") == ModelPrice(input=1, output=1, cache_write=1, cache_read=1)
    assert table.price_for("claude-sonnet-5-5") is not None  # defaults kept
