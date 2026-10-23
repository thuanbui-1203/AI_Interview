import asyncio
import json
from datetime import date

from medextract.config import Settings
from medextract.core.pipeline import Extractor
from medextract.errors import ProviderUnavailable
from medextract.evaluation.harness import flagged_for_review, run_eval
from medextract.providers.fake import FakeProvider
from tests.helpers import classification, png_bytes, receipt_payload

F = {
    "grand_total": {"value": 100.0, "confidence": 0.97},
    "payment_method": {"value": None, "confidence": 0.1},
    "items": {"value": [{"description": "A", "total": 1.0}, {"description": "B", "total": None}], "confidence": 0.97},
    "date": {"value": "2024-01-01", "confidence": 0.75},
}


def test_flagged_for_review_paths():
    assert not flagged_for_review(F, "grand_total")
    assert flagged_for_review(F, "payment_method")  # null
    assert flagged_for_review(F, "date")  # below the review threshold
    assert flagged_for_review(F, "items[1].total")  # null cell inside a confident table
    assert not flagged_for_review(F, "items[0].total")
    assert flagged_for_review(F, "items[5].total")  # row missing entirely
    assert flagged_for_review(F, "nonexistent")


def make_samples(tmp, n=3):
    for i in range(n):
        d = tmp / "core" / f"doc{i}"
        d.mkdir(parents=True)
        (d / "document.png").write_bytes(png_bytes())
        truth = {"doc_id": f"doc{i}", "set": "core", "document_type": "receipt", "file": "document.png",
                 "fields": {"grand_total": 500000.0}, "expected_issues": [], "low_confidence_fields": []}  # fmt: skip
        (d / "truth.json").write_text(json.dumps(truth), encoding="utf-8")


def extractor(handler):
    return Extractor(settings=Settings(), provider=FakeProvider(handler), today=lambda: date(2024, 6, 1))


def test_daily_quota_stops_scheduling_and_is_not_scored_as_wrong(tmp_path):
    make_samples(tmp_path / "s")
    calls = []

    def handler(req):
        calls.append(req.stage)
        if len(calls) == 3:  # second document's classify call hits the daily quota
            raise ProviderUnavailable("Gemini daily quota exhausted: limit 20")
        return classification() if req.stage == "classify" else receipt_payload()

    r = asyncio.run(run_eval(tmp_path / "s", tmp_path / "out", ["core"], extractor(handler), concurrency=1))
    assert r["documents"] == 3 and r["documents_scored"] == 1
    assert len(r["errors"]) == 1 and r["skipped"] == ["doc2"]
    assert r["classification_accuracy"] == 1.0  # failures are not counted as misclassifications
    assert len(calls) == 3  # nothing was sent after the quota error


def test_resume_rescores_saved_results_without_calling_the_api(tmp_path):
    make_samples(tmp_path / "s", n=2)
    ok = lambda req: classification() if req.stage == "classify" else receipt_payload()  # noqa: E731
    asyncio.run(run_eval(tmp_path / "s", tmp_path / "out", ["core"], extractor(ok), concurrency=1))

    def boom(req):
        raise AssertionError("resume must not call the provider")

    r = asyncio.run(run_eval(tmp_path / "s", tmp_path / "out", ["core"], extractor(boom), resume=True))
    assert r["documents_scored"] == 2 and r["documents_resumed"] == 2 and r["field_accuracy"] == 1.0
