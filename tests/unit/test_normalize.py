from medextract.core.normalize import normalize
from medextract.core.registry import default_registry
from medextract.doctypes.discharge_summary import DischargeSummaryExtraction
from medextract.doctypes.lab_report import LabReportExtraction
from medextract.doctypes.receipt import ReceiptExtraction
from tests.helpers import date_, make_doc, money, receipt_payload, rows, text

REG = default_registry()


def run(payload, model=ReceiptExtraction, spec="receipt", doc=None):
    return normalize(model.model_validate(payload), REG.get(spec), doc)


def test_public_values_follow_contract():
    n = run(receipt_payload())
    f = n.fields
    assert f["hospital_name"].value == "Bệnh viện Đa khoa Hòa Bình"
    assert f["date"].value == "2024-03-12"
    assert f["grand_total"].value == 500000.0
    assert f["payment_method"].value == "cash"
    assert f["items"].value[0] == {
        "description": "Khám bệnh", "quantity": 1.0, "unit_price": 150000.0, "total": 150000.0, "category": "consultation",
    }  # fmt: skip
    assert set(f) == set(REG.get("receipt").output_fields)
    assert n.currency == "VND" and n.language == "vi"


def test_null_fields_get_low_confidence():
    f = run(receipt_payload()).fields
    assert f["invoice_number"].value is None
    assert f["invoice_number"].confidence <= 0.15
    assert f["subtotal"].value is None and f["subtotal"].confidence <= 0.15


def test_vnd_thousands_misread_is_overridden_by_raw_parse():
    # The model read "15.750.000 ₫" as 15.75 - a classic separator error.
    n = run(receipt_payload(grand_total=money("15.750.000 ₫", 15.75, evidence="Tổng cộng: 15.750.000 ₫")))
    gt = n.fields["grand_total"]
    assert gt.value == 15_750_000
    assert gt.confidence <= 0.5
    assert any(i.code == "VALUE_RAW_MISMATCH" for i in n.issues)


def test_invalid_printed_date_becomes_null_but_keeps_raw():
    n = run(receipt_payload(date=date_("31/02/2024", "2024-02-31")))
    d = n.fields["date"]
    assert d.value is None and d.raw == "31/02/2024" and d.confidence <= 0.15


def test_vietnamese_date_is_day_first_even_if_model_swapped():
    n = run(receipt_payload(date=date_("03/04/2024", "2024-03-04")))
    assert n.fields["date"].value == "2024-04-03"  # vi -> day first
    assert any(i.code == "VALUE_RAW_MISMATCH" for i in n.issues)


def test_ambiguous_english_date_keeps_model_choice_and_warns():
    p = receipt_payload(document_language="en", date=date_("03/04/2027", "2027-03-04"))
    n = run(p)
    assert n.fields["date"].value == "2027-03-04"  # model's (swapped) reading is a valid interpretation
    assert n.fields["date"].confidence <= 0.75
    assert any(i.code == "DATE_AMBIGUOUS_ORDER" for i in n.issues)


def test_value_without_evidence_is_capped():
    n = run(receipt_payload(patient_name=text("NGUYỄN VĂN AN", evidence=None)))
    assert n.fields["patient_name"].confidence <= 0.5


def test_evidence_missing_from_text_layer_is_penalized():
    doc = make_doc(["HÓA ĐƠN Bệnh viện Đa khoa Hòa Bình Họ tên: NGUYỄN VĂN AN Tổng cộng: 500.000 ₫ Tiền mặt"])
    payload = receipt_payload(patient_name=text("TRẦN THỊ BÌNH", evidence="Họ tên: TRẦN THỊ BÌNH"))
    n = run(payload, doc=doc)
    assert n.fields["patient_name"].confidence < 0.7
    assert any(a.reason == "evidence_not_in_text_layer" for a in n.fields["patient_name"].trace)
    assert n.fields["hospital_name"].confidence > 0.9  # grounded field untouched


def test_table_not_found_is_null_and_empty_table_is_list():
    n = run(receipt_payload(items=rows(status="not_found")))
    assert n.fields["items"].value is None
    payload = {
        "observed_document_type": "discharge_summary",
        "document_language": "en",
        "hospital_name": text("Riverside"),
        "patient_name": text("John Tan"),
        "admission_date": date_("12/03/2024", "2024-03-12"),
        "discharge_date": date_("15/03/2024", "2024-03-15"),
        "diagnosis": {"evidence": "Dx: Pneumonia (J18.9)", "page": 1, "status": "present", "primary": {"description": "Pneumonia", "icd10_code": "J18.9"}, "secondary": [], "confidence": "certain"},
        "procedures_performed": rows(status="present"),
        "attending_physician": text("Dr. Lim"),
        "discharge_instructions": text("Rest"),
        "department": text(None),
        "discharge_status": {"evidence": None, "page": None, "status": "not_found", "raw": None, "value": None, "confidence": "none"},
        "follow_up_date": date_(None, None),
        "sick_leave_from": date_(None, None),
        "sick_leave_to": date_(None, None),
    }
    d = run(payload, DischargeSummaryExtraction, "discharge_summary")
    assert d.fields["procedures_performed"].value == []
    assert d.fields["diagnosis"].value == {"primary": {"description": "Pneumonia", "icd10_code": "J18.9"}, "secondary": []}


def test_lab_flags_printed_vs_derived():
    payload = {
        "observed_document_type": "lab_report",
        "document_language": "en",
        "lab_name": text("Central Lab"),
        "patient_name": text("Mai Linh"),
        "date": date_("12/03/2024", "2024-03-12"),
        "tests": rows(
            {"evidence": "Potassium 6.8 mmol/L 3.5-5.0", "page": 1, "test_name": "Potassium", "result": "6.8", "unit": "mmol/L", "reference_range": "3.5-5.0", "flag": None, "confidence": "certain"},
            {"evidence": "Glucose 5.1 mmol/L 3.9-6.1 N", "page": 1, "test_name": "Glucose", "result": "5.1", "unit": "mmol/L", "reference_range": "3.9-6.1", "flag": "normal", "confidence": "certain"},
            {"evidence": "HBsAg Negative", "page": 1, "test_name": "HBsAg", "result": "Negative", "unit": None, "reference_range": "Negative", "flag": None, "confidence": "certain"},
        ),
        "ordering_physician": text(None),
        "collection_date": date_(None, None),
    }
    t = run(payload, LabReportExtraction, "lab_report").fields["tests"].value
    assert (t[0]["flag"], t[0]["flag_source"]) == ("high", "derived")
    assert (t[1]["flag"], t[1]["flag_source"]) == ("normal", "printed")
    assert (t[2]["flag"], t[2]["flag_source"]) == (None, None)


def test_list_confidence_is_min_of_rows():
    items = rows(
        {"evidence": "A 1 10 10", "page": 1, "description": "A", "quantity": 1, "unit_price": 10, "total": 10, "category": "other", "confidence": "certain"},
        {"evidence": "B 1 ?? 5", "page": 1, "description": "B", "quantity": 1, "unit_price": None, "total": 5, "category": "other", "confidence": "low"},
    )
    assert run(receipt_payload(items=items)).fields["items"].confidence == 0.35


def test_magnitude_fields_drop_the_printed_sign():
    # Live finding (Gemini, core_r01): "Less: AIA direct billing  -10,000.00" - the model correctly
    # read 10000, and keeping the printed minus caused AMOUNT_NOT_POSITIVE + PAYABLE_CHAIN false alarms.
    n = run(receipt_payload(
        insurance_covered_amount=money("-10,000.00", 10000.0, evidence="Less: AIA direct billing -10,000.00"),
        discount_total=money("(1.332.000)", -1332000.0, evidence="Giảm giá (1.332.000)"),
    ))  # fmt: skip
    assert n.fields["insurance_covered_amount"].value == 10000.0
    assert n.fields["discount_total"].value == 1332000.0
    assert not any(i.code == "VALUE_RAW_MISMATCH" for i in n.issues)
    assert n.fields["insurance_covered_amount"].confidence > 0.9


def test_sign_still_matters_for_other_money_fields():
    n = run(receipt_payload(grand_total=money("-500.000 ₫", 500000.0, evidence="Tổng cộng: -500.000 ₫")))
    assert n.fields["grand_total"].value == -500000.0  # a negative total is a real anomaly: keep it visible


def test_normalized_values_are_checked_against_printed_wording():
    # Live finding: 'cash' / 'VND' are normalizations - never verbatim in the quote.
    n = run(receipt_payload(currency=text("VND", evidence="Đơn giá (đồng)")))
    f = n.fields
    assert f["currency"].confidence > 0.9 and f["payment_method"].confidence > 0.9
    assert not any(a.reason == "value_not_in_evidence" for x in f.values() for a in x.trace)


def test_row_cell_not_in_row_evidence_lowers_row_confidence():
    # Live finding (Gemini, stress_s06): result '1+' came back as '1 technique'.
    from tests.helpers import rows as _rows

    payload = {
        "observed_document_type": "lab_report",
        "document_language": "en",
        "lab_name": text("Central Lab"),
        "patient_name": text("Mai Linh"),
        "date": date_("12/03/2024", "2024-03-12"),
        "tests": _rows(
            {"evidence": "Urine Glucose 1+ Negative", "page": 1, "test_name": "Urine Glucose", "result": "1 technique", "unit": None, "reference_range": "Negative", "flag": None, "confidence": "certain"},
            {"evidence": "Abbreviated very long test na... 5.1 mmol/L 3.9-6.1", "page": 1, "test_name": "Abbreviated very long test name for glucose", "result": "5.1", "unit": "mmol/L", "reference_range": "3.9-6.1", "flag": None, "confidence": "certain"},
        ),
        "ordering_physician": text(None),
        "collection_date": date_(None, None),
    }  # fmt: skip
    f = run(payload, LabReportExtraction, "lab_report").fields["tests"]
    assert f.confidence < 0.85
    reasons = [a.reason for a in f.trace]
    assert "row0:result_not_in_evidence" in reasons
    assert not any(r.startswith("row1:") for r in reasons)  # abbreviated name is not penalized
    assert f.row_evidence == ["Urine Glucose 1+ Negative", "Abbreviated very long test na... 5.1 mmol/L 3.9-6.1"]
