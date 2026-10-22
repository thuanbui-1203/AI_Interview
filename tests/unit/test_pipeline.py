import asyncio
from datetime import date

import pytest

from medextract.config import Settings
from medextract.core.pipeline import Extractor
from medextract.errors import ConfigError, ModelRefusal
from medextract.providers.fake import FakeProvider
from tests.helpers import classification, make_doc, money, png_bytes, receipt_payload, text


def extractor(handler, **settings):
    return Extractor(settings=Settings(**settings), provider=FakeProvider(handler), today=lambda: date(2024, 6, 1))


def run(ex, data=None, **kw):
    return asyncio.run(ex.extract_bytes(data or png_bytes(), source_name="x.png", **kw))


def test_end_to_end_public_shape():
    ex = extractor({"classify": [classification()], "extract": [receipt_payload()]})
    out = run(ex).to_public()
    assert set(out) == {"document_type", "confidence", "fields", "validation_errors"}
    assert out["document_type"] == "receipt" and out["confidence"] == pytest.approx(0.95)
    assert out["fields"]["grand_total"] == {"value": 500000.0, "confidence": pytest.approx(0.97)}
    assert all(set(v) == {"value", "confidence"} for v in out["fields"].values())
    assert all(set(i) == {"code", "severity", "fields", "message"} for i in out["validation_errors"])


def test_two_stage_calls_and_settings_are_used():
    ex = extractor({"classify": [classification()], "extract": [receipt_payload()]}, classify_model="m-small", extract_model="m-big")
    run(ex)
    reqs = ex.provider.requests
    assert [r.stage for r in reqs] == ["classify", "extract"]
    assert reqs[0].model == "m-small" and reqs[1].model == "m-big"
    assert reqs[1].output_model.__name__ == "ReceiptExtraction"
    assert "Trust boundary" in reqs[1].system and "RECEIPT" in reqs[1].system


def test_expected_type_skips_classification():
    ex = extractor({"extract": [receipt_payload()]})
    res = run(ex, expected_type="receipt")
    assert [r.stage for r in ex.provider.requests] == ["extract"]
    assert res.confidence == 1.0


def test_unknown_expected_type_is_config_error():
    with pytest.raises(ConfigError):
        run(extractor({}), expected_type="invoice")


def test_required_missing_and_total_mismatch_are_reported():
    payload = receipt_payload(payment_method={"evidence": None, "page": None, "status": "not_found", "raw": None, "value": None, "confidence": "none"},
                              grand_total=money("900.000 ₫", 900000.0, evidence="Tổng cộng: 900.000 ₫"))  # fmt: skip
    res = run(extractor({"classify": [classification()], "extract": [payload]}))
    codes = {i.code for i in res.validation_errors}
    assert {"REQUIRED_FIELD_MISSING", "TOTAL_MISMATCH"} <= codes
    assert res.fields["grand_total"].confidence <= 0.5  # validation caps the field it implicates


def test_disagreement_between_classifier_and_extractor():
    res = run(extractor({"classify": [classification("receipt")], "extract": [receipt_payload(observed_document_type="prescription")]}))
    assert res.confidence <= 0.5
    assert "CLASSIFICATION_DISAGREEMENT" in {i.code for i in res.validation_errors}


def test_non_medical_input_is_forced_but_flagged():
    res = run(extractor({"classify": [classification(medical=False)], "extract": [receipt_payload()]}))
    assert res.document_type == "receipt" and res.confidence <= 0.3
    assert "NOT_MEDICAL_DOCUMENT_SUSPECTED" in {i.code for i in res.validation_errors}


def test_classifier_scores_are_clamped_renormalised_and_consistent_with_label():
    from medextract.core.steps import _normalized_scores

    s = _normalized_scores({"receipt": 2.0, "lab_report": -1.0, "prescription": 0.0}, "receipt")
    assert s == {"receipt": 1.0, "lab_report": 0.0, "prescription": 0.0}
    s = _normalized_scores({"receipt": 0.2, "lab_report": 0.6, "prescription": 0.2}, "receipt")
    assert s["receipt"] == s["lab_report"] == 0.6  # label disagrees with numbers: label keeps the max
    s = _normalized_scores({"receipt": 0.0, "lab_report": 0.0}, "lab_report")
    assert s == {"receipt": 0.0, "lab_report": 1.0}


def test_provider_errors_propagate():
    with pytest.raises(ModelRefusal):
        run(extractor({"classify": [ModelRefusal("refused")]}))


def test_buggy_rule_is_isolated(monkeypatch):
    import medextract.core.pipeline as P

    class Boom:
        code = "BOOM"

        def check(self, fields, ctx):
            raise RuntimeError("bug")

    monkeypatch.setattr(P, "rules_for", lambda name: [Boom()])
    res = run(extractor({"classify": [classification()], "extract": [receipt_payload()]}))
    assert any(i.code == "RULE_ERROR" for i in res.validation_errors)


def test_pages_are_labelled_for_the_model():
    ex = extractor({"classify": [classification()], "extract": [receipt_payload()]})
    asyncio.run(ex.extract_document(make_doc([None, None])))
    texts = [p.text for p in ex.provider.requests[1].content if hasattr(p, "text")]
    assert texts[:2] == ["Page 1 of 2", "Page 2 of 2"]


def test_strategy_not_implemented_is_explicit():
    with pytest.raises(ConfigError):
        run(extractor({}, strategy="single_call"))


def test_verbose_output_has_audit_data():
    res = run(extractor({"classify": [classification()], "extract": [receipt_payload(patient_name=text("NGUYỄN VĂN AN", evidence=None))]}))
    v = res.to_public(verbose=True)
    assert v["fields"]["patient_name"]["trace"][0]["reason"] == "no_evidence"
    assert v["meta"]["calls"][0]["stage"] == "classify" and "total_cost_usd" in v["meta"]
