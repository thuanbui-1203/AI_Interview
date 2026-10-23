import pytest

from medextract import confidence as C
from medextract.core.models import ClassificationInfo, FieldResult, ValidationIssue
from medextract.evaluation.harness import calibration
from medextract.evaluation.matchers import compare, text_equal


# --- confidence -------------------------------------------------------------------------------
def cls(top=0.95, second=0.03, t="receipt", medical=True):
    return ClassificationInfo(
        document_type=t, confidence=top, scores={"receipt": top, "lab_report": second}, is_medical_document=medical
    )


def test_document_confidence_clear_case():
    conf, issues = C.document_confidence(cls(), "receipt")
    assert conf == pytest.approx(0.95) and issues == []


def test_document_confidence_small_margin_and_disagreement():
    conf, _ = C.document_confidence(cls(0.5, 0.45), "receipt")
    assert conf == pytest.approx(0.5 - 0.5 * (0.3 - 0.05))
    conf, issues = C.document_confidence(cls(), "lab_report")
    assert conf == 0.5 and {i.code for i in issues} >= {"CLASSIFICATION_DISAGREEMENT", "CLASSIFICATION_LOW_CONFIDENCE"}


def test_observed_type_comparison_ignores_case_and_spaces():
    conf, issues = C.document_confidence(cls(), " Receipt ")
    assert conf == pytest.approx(0.95) and issues == []


def test_non_medical_document_is_capped_and_flagged():
    conf, issues = C.document_confidence(cls(medical=False), "receipt")
    assert conf <= 0.3
    assert "NOT_MEDICAL_DOCUMENT_SUSPECTED" in {i.code for i in issues}


def test_issue_caps_apply_to_named_fields_only():
    fields = {"grand_total": FieldResult(value=100.0, confidence=0.97), "date": FieldResult(value="2024-01-01", confidence=0.97)}
    issues = [ValidationIssue(code="TOTAL_MISMATCH", severity="error", fields=["grand_total", "items[1].total"], message="x")]
    C.apply_issue_caps(fields, issues)
    assert fields["grand_total"].confidence == 0.5 and fields["date"].confidence == 0.97
    assert fields["grand_total"].trace[-1].reason == "issue:TOTAL_MISMATCH"


@pytest.mark.parametrize(
    ("value", "evidence", "ok"),
    [
        (15750000.0, "Tổng cộng: 15.750.000 ₫", True),
        (15.75, "Tổng cộng: 15.750.000 ₫", False),
        (1234.5, "Total THB 1,234.50", True),
        ("2024-03-12", "ngày 12 tháng 03 năm 2024", True),
        ("2024-03-12", "Date: 15/03/2024", False),
        ("NGUYỄN VĂN AN", "Họ tên: Nguyễn Văn An", True),
        ("Lê Thị Hoa", "Patient: John Smith", False),
        (10.0, "A 1 10 10", True),  # separate numbers must not be glued together
        (1250000.0, "Tổng: 1 250 000 đ", True),  # space-grouped thousands
        (150000.0, "Khám bệnh 1 150.000 150.000", True),
        (1150000.0, "Khám bệnh 1 150.000 150.000", False),
    ],
)
def test_value_supported_by_evidence(value, evidence, ok):
    assert C.value_supported_by(value, evidence) is ok


def test_evidence_in_text_layer_is_diacritic_and_case_aware():
    page = "BỆNH VIỆN ĐA KHOA HÒA BÌNH\nHọ tên người bệnh: NGUYỄN VĂN AN"
    assert C.evidence_in_text("Họ tên người bệnh: Nguyễn Văn An", page)
    assert not C.evidence_in_text("Họ tên người bệnh: Nguyen Van Binh", page)


# --- matchers / calibration ------------------------------------------------------------------
def test_compare_verdicts():
    assert compare("x", None, None).verdict == "correct"
    assert compare("x", "a", None).verdict == "hallucinated"
    assert compare("x", None, "a").verdict == "missed"
    assert compare("grand_total", 100.001, 100.0).verdict == "correct"
    assert compare("grand_total", 101.0, 100.0).verdict == "wrong"


def test_text_equal_tolerates_whitespace_case_not_different_names():
    assert text_equal("NGUYỄN  VĂN AN", "Nguyễn Văn An")
    assert not text_equal("Nguyễn Văn An", "Nguyễn Văn Ân Bình")


def test_compare_rows_matches_by_key_not_order():
    truth = [{"description": "A", "total": 1.0}, {"description": "Blood test", "total": 2.0}]
    pred = [{"description": "Blood test", "total": 2.0}, {"description": "A", "total": 1.0}]
    o = compare("items", pred, truth)
    assert o.correct and o.cells_correct == o.cells_total == 4
    o = compare("items", pred[:1], truth)
    assert not o.correct and o.cells_correct == 2


def test_compare_diagnosis():
    t = {"primary": {"description": "Viêm ruột thừa cấp", "icd10_code": "K35.8"}, "secondary": []}
    assert compare("diagnosis", {"primary": {"description": "Viêm ruột thừa cấp", "icd10_code": "k35.8"}, "secondary": []}, t).correct
    assert not compare("diagnosis", {"primary": {"description": "Viêm ruột thừa cấp", "icd10_code": "K35.9"}, "secondary": []}, t).correct


def test_calibration_perfect_and_overconfident():
    perfect = calibration([(0.95, True)] * 10 + [(0.1, False)] * 10)
    assert perfect["ece"] == pytest.approx(0.075, abs=1e-6)
    over = calibration([(0.95, False)] * 10)
    assert over["ece"] == pytest.approx(0.95) and over["accuracy_at_0.9"] == 0.0


def test_calibration_excludes_null_predictions():
    from medextract.evaluation.harness import DocEval, FieldEval, summarize
    from medextract.evaluation.matchers import Outcome

    d = DocEval(doc_id="x", set="core", truth_type="receipt", pred_type="receipt")
    d.fields = [
        FieldEval("a", True, Outcome("correct", cells_correct=1), 0.97, False, False),
        FieldEval("b", False, Outcome("correct", cells_correct=1), 0.10, True, True),  # correct abstention
        FieldEval("c", False, Outcome("missed"), 0.10, False, True),  # wrong abstention
    ]
    r = summarize([d])
    assert r["calibration"]["bins"][-1]["n"] == 1 and sum(b["n"] for b in r["calibration"]["bins"]) == 1
    assert r["null_predictions"] == 2 and r["null_precision"] == 0.5


def test_category_is_not_scored():
    truth = [{"description": "Nursing care", "total": 10.0, "category": "other"}]
    assert compare("items", [{"description": "Nursing care", "total": 10.0, "category": "room"}], truth).correct


@pytest.mark.parametrize(
    ("value", "evidence"),
    [
        ("Complete the full course of antibiotics. Rest at home and drink plenty of water for 7 days.",
         "Instructions: Complete the full course of antibiotics. Rest at home"),
    ],
)  # fmt: skip
def test_long_text_quote_is_a_slice_of_the_value(value, evidence):
    assert C.value_supported_by(value, evidence)
