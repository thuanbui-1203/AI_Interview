"""Golden tests: replay recorded Gemini responses offline and score them against truth.json.

The cassette key covers model, prompt, schema and page images, so a prompt or schema change makes
the affected documents skip (re-record them) instead of silently replaying stale answers. What is
replayed must classify correctly, raise every expected validation issue, and get every field right
except the ones declared in known_failures.json.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import pytest

from medextract.config import Settings
from medextract.core.pipeline import Extractor
from medextract.core.registry import default_registry
from medextract.evaluation.harness import DocEval, load_truths, score_output
from medextract.providers.cassette import CassetteMiss

ROOT = Path(__file__).resolve().parents[2]
CASSETTES = ROOT / "tests" / "cassettes" / "gemini"
KNOWN = json.loads((Path(__file__).parent / "known_failures.json").read_text(encoding="utf-8"))
TRUTHS = load_truths(ROOT / "samples", ["core", "stress"])
REQUIRED = {s.name: set(s.required_fields) for s in default_registry().all()}
RECORDED_ON = date(2026, 10, 22)  # DATE_IN_FUTURE is relative to "today": pin it to the recording day


@pytest.fixture(scope="module")
def extractor() -> Extractor:
    settings = Settings(_env_file=None, provider="gemini", record_mode="replay", cassette_dir=CASSETTES)
    return Extractor(settings=settings, today=lambda: RECORDED_ON)


@pytest.mark.parametrize(("path", "truth"), TRUTHS, ids=[t["doc_id"] for _, t in TRUTHS])
def test_replayed_extraction_matches_truth(extractor: Extractor, path: Path, truth: dict) -> None:
    try:
        result = asyncio.run(extractor.extract_bytes(path.read_bytes(), source_name=path.name))
    except CassetteMiss:
        pytest.skip("no cassette for the current prompt/schema - re-record with MEDX_RECORD_MODE=auto")
    d = DocEval(
        doc_id=truth["doc_id"],
        set=truth["set"],
        truth_type=truth.get("document_type"),
        expected_issues=list(truth.get("expected_issues") or []),
    )
    score_output(d, result.to_public(verbose=True), truth, REQUIRED)

    assert d.classified_ok, f"type {d.pred_type!r} (conf {d.doc_confidence}) vs truth {d.truth_type!r}"
    assert not d.missing_issues, f"expected validation issues not raised: {d.missing_issues}"
    allowed = set(KNOWN.get(d.doc_id, {}))
    wrong = {f.name: f"{f.outcome.verdict} {f.outcome.detail}" for f in d.fields if not f.outcome.correct}
    unexpected = {k: v for k, v in wrong.items() if k not in allowed}
    assert not unexpected, f"fields wrong beyond known_failures.json: {unexpected}"
    for name in allowed & set(wrong):  # a known failure must at least be flagged for a human
        assert result.fields[name].confidence < 0.85, f"{name} is wrong but not flagged"
