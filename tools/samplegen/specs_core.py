"""CORE set: 10 cleanly classifiable documents (3 receipts, 3 discharge, 2 lab, 2 prescriptions).

All facilities, people, tax codes and addresses are fictional.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from samplegen.model import SampleDoc, vn_amount_in_words


def receipt_data(**kw: Any) -> dict[str, Any]:
    """Receipt data with every optional summary line explicitly absent unless given."""
    base: dict[str, Any] = dict.fromkeys(
        ["subtotal", "discount_total", "tax_amount", "insurance_covered_amount", "patient_payable", "amount_in_words",
         "invoice_number", "patient_name", "payment_method", "payment_label", "currency"]
    )  # fmt: skip
    base.update(kw)
    return base


def item(desc: str, qty: float | None, price: float | None, total: float | None, cat: str, **extra: Any) -> dict[str, Any]:
    return {"description": desc, "quantity": qty, "unit_price": price, "total": total, "category": cat, **extra}


def line(desc: str, qty: float, price: float, cat: str, **extra: Any) -> dict[str, Any]:
    """An item whose printed total is exactly qty x price."""
    return item(desc, qty, price, round(qty * price, 2), cat, **extra)


# --------------------------------------------------------------------------------------------
# receipts
# --------------------------------------------------------------------------------------------
def _r01() -> SampleDoc:
    items = [
        line("Consultation fee - Internal Medicine (Dr. Kittipong Wongsuwan)", 1, 1200.00, "consultation", code="OPD-0101"),
        line("Ward room - Standard single, per night", 2, 3500.00, "room", code="IPD-2201"),
        line("Nursing care fee, per day", 2, 800.00, "other", code="IPD-2310"),
        line("Complete Blood Count (CBC)", 1, 450.00, "lab", code="LAB-1001"),
        line("Chest X-ray PA view", 1, 900.00, "imaging", code="RAD-3002"),
        line("Ceftriaxone 1 g injection", 4, 380.00, "drug", code="PHA-5512"),
        line("IV set and medical supplies", 1, 650.00, "consumable", code="SUP-7004"),
    ]
    gross = sum(i["total"] for i in items)  # 13,320.00
    discount = round(gross * 0.10, 2)
    items.append(item("Corporate discount 10% (Siam Northstar Trading Co., Ltd.)", None, None, -discount, "discount", code="DSC-CORP"))
    subtotal = round(gross - discount, 2)
    vat = round(subtotal * 0.07, 2)
    grand = round(subtotal + vat, 2)
    insured = 10000.00
    data = receipt_data(
        facility={
            "name": "Chao Phraya Meridian Hospital",
            "address": "88/9 Rama IX Road, Huai Khwang, Bangkok 10310, Thailand",
            "phone": "+66 2 118 4400",
            "tax_label": "Tax ID",
            "tax_code": "0105561234567",
            "website": "www.cpmeridian-hospital.example",
            "logo": {"kind": "shield"},
        },
        accent="#0b6e8a",
        title="RECEIPT / TAX INVOICE",
        subtitle="Inpatient - Original",
        currency="THB",
        currency_note="Thai Baht (THB)",
        money_style="en2",
        date=date(2025, 8, 14),
        date_fmt="%d %b %Y",
        invoice_number="INV-CPM-2508-01472",
        patient_name="Pimchanok Srisawat",
        patient_info=[
            ("HN", "HN 66-0048213"),
            ("AN", "AN 25-011902"),
            ("Admitted", "12 Aug 2025"),
            ("Discharged", "14 Aug 2025"),
        ],
        show_codes=True,
        items=items,
        subtotal=subtotal,
        tax_amount=vat,
        grand_total=grand,
        insurance_covered_amount=insured,
        patient_payable=round(grand - insured, 2),
        labels={
            "subtotal": "Subtotal (after discount)",
            "tax": "VAT 7%",
            "grand_total": "TOTAL (incl. VAT)",
            "insurance": "Less: Direct billing - Pacifica Shield Insurance (Policy PSI-7712094)",
            "payable": "Amount Paid by Patient",
        },
        payment_method="card",
        payment_label="Credit card - VISA ending 4821",
        payment_details=[("Approval code", "084417"), ("Terminal", "CPM-CASH-03")],
        cashier="Nattaya Boonmee",
        footnotes=[
            "Direct billing amount is subject to final adjudication by the insurer.",
            "Corporate discount applied under agreement no. CA-2024-118.",
        ],
        stamp={"top": "CHAO PHRAYA MERIDIAN HOSPITAL", "bottom": "CASHIER", "center": ["PAID", "14 AUG 2025"]},
    )
    return SampleDoc(
        doc_id="core_r01_en_thb", set="core", doc_type="receipt", language="en", variant="clean",
        template="receipt_en_hospital.html.j2", data=data,
        notes="EN private hospital receipt in THB: discount row (negative), VAT 7% as tax_amount, insurer direct billing -> insurance_covered_amount and patient_payable, card payment.",
    )  # fmt: skip


def _r02() -> SampleDoc:
    groups = [
        {"label": "1. Khám bệnh", "items": [
            line("Khám Nội tổng quát", 1, 150000, "consultation", unit="Lần"),
        ]},
        {"label": "2. Xét nghiệm", "items": [
            line("Tổng phân tích tế bào máu ngoại vi (bằng máy đếm laser)", 1, 95000, "lab", unit="Lần"),
            line("Định lượng Glucose [Máu]", 1, 30000, "lab", unit="Lần"),
            line("Định lượng HbA1c [Máu]", 1, 350000, "lab", unit="Lần"),
        ]},
        {"label": "3. Chẩn đoán hình ảnh, thăm dò chức năng", "items": [
            line("Siêu âm ổ bụng tổng quát", 1, 250000, "imaging", unit="Lần"),
            line("Chụp X-quang ngực thẳng", 1, 180000, "imaging", unit="Lần"),
            line("Điện tim thường", 1, 72000, "procedure", unit="Lần"),
        ]},
        {"label": "4. Thuốc, vật tư y tế", "items": [
            line("Metformin 500mg", 28, 1500, "drug", unit="Viên"),
            line("Omeprazol 20mg", 14, 3500, "drug", unit="Viên"),
            line("Bơm tiêm nhựa 5ml", 2, 4000, "consumable", unit="Cái"),
        ]},
    ]  # fmt: skip
    items = [i for g in groups for i in g["items"]]
    for n, it in enumerate(items, 1):
        it["no"] = n
    grand = sum(i["total"] for i in items)
    insured = round(grand * 0.8)
    payable = grand - insured
    data = receipt_data(
        facility={
            "authority": "SỞ Y TẾ TP. HỒ CHÍ MINH",
            "name": "Bệnh viện Đa khoa Thanh Trúc Lâm",
            "address": "215 Đường Lạc Long Quân, Phường 10, Quận Tân Bình, TP. Hồ Chí Minh",
            "phone": "028 3866 4021",
            "tax_code": "0318765432",
        },
        title="BẢNG KÊ CHI PHÍ KHÁM CHỮA BỆNH NGOẠI TRÚ",
        subtitle="Kiêm hóa đơn viện phí",
        currency="VND",
        invoice_number="BK-2403-005812",
        patient_code="24031805812",
        patient_name="Nguyễn Thị Hồng Nhung",
        dob="14/09/1978",
        sex="Nữ",
        patient_address="47/12 Đường Nguyễn Văn Đậu, Phường 6, Quận Bình Thạnh, TP. Hồ Chí Minh",
        insurance_card="GD4797925012345",
        primary_care="Trạm Y tế Phường 6",
        visit_time="07 giờ 45 phút, ngày 18/03/2024",
        diagnosis_text="E11.9 - Bệnh đái tháo đường không phụ thuộc insulin",
        coverage_rate="80%",
        groups=groups,
        items=items,
        grand_total=float(grand),
        insurance_covered_amount=float(insured),
        patient_payable=float(payable),
        amount_in_words=vn_amount_in_words(int(payable)),
        payment_method="cash",
        payment_label="Tiền mặt",
        place="TP. Hồ Chí Minh",
        date=date(2024, 3, 18),
        cashier="Trương Mỹ Linh",
        accountant="Hồ Văn Tài",
        printed_at="10:42 18/03/2024",
        stamp={"top": "BỆNH VIỆN ĐA KHOA THANH TRÚC LÂM", "bottom": "TP. HỒ CHÍ MINH", "center": ["ĐÃ THU TIỀN"]},
    )
    return SampleDoc(
        doc_id="core_r02_vi_vnd", set="core", doc_type="receipt", language="vi", variant="clean",
        template="receipt_vn_bangke.html.j2", data=data,
        notes="VN outpatient cost statement in VND with '.' thousands separators; BHYT fund pays 80% (insurance_covered_amount), 'Người bệnh trả' = patient_payable; 'Bằng chữ' matches patient_payable; group header rows are not items.",
    )  # fmt: skip


def _r03() -> SampleDoc:
    items = [
        line("General consultation", 1, 45.00, "consultation"),
        line("Rapid strep throat swab test", 1, 28.00, "lab"),
        line("Amoxicillin 500 mg capsules", 21, 0.60, "drug"),
        line("Paracetamol 500 mg tablets", 20, 0.15, "drug"),
    ]
    data = receipt_data(
        facility={
            "name": "Larkspur Family Clinic",
            "address": "Blk 412 Larkspur Avenue 3, #01-27, Singapore 560412",
            "phone": "+65 6455 0192",
            "tax_label": "UEN",
            "tax_code": "201733418K",
            "logo": {"kind": "leaf"},
        },
        accent="#2f6b3a",
        currency="SGD",
        money_style="en2",
        invoice_number="LFC-26-00318",
        patient_name="Tan Wei Ling",
        patient_info=[("NRIC (last 4)", "xxxx512D"), ("Visit type", "Walk-in")],
        date=date(2026, 2, 9),
        date_fmt="%d-%b-%Y",
        time="10:24",
        items=items,
        grand_total=round(sum(i["total"] for i in items), 2),
        payment_method="cash",
        payment_label="CASH",
        cashier="Siti Nurhaliza",
        stamp={"top": "LARKSPUR FAMILY CLINIC", "bottom": "SINGAPORE", "center": ["PAID"], "color": "#1f4fa8"},
    )
    return SampleDoc(
        doc_id="core_r03_en_sgd_scan", set="core", doc_type="receipt", language="en", variant="scan",
        template="receipt_en_clinic.html.j2", data=data, degrade="scan",
        notes="Small EN clinic receipt in SGD, cash, 4 items, delivered as a scanned PNG (no text layer).",
    )  # fmt: skip


# --------------------------------------------------------------------------------------------
# discharge summaries
# --------------------------------------------------------------------------------------------
_NVGH = {
    "name": "Northbridge Valley General Hospital",
    "address": "1200 Alder Crest Parkway, Northbridge, NV 89511",
    "phone": "+1 (775) 555-0148",
    "logo": {"kind": "cross"},
}


def _d01() -> SampleDoc:
    data = {
        "facility": _NVGH,
        "accent": "#24427a",
        "patient_name": "Margaret A. Collins",
        "mrn": "NV-0084172",
        "dob": "17 June 1961",
        "sex": "Female",
        "admission": date(2025, 5, 3),
        "discharge": date(2025, 5, 9),
        "department": "Pulmonology",
        "ward": "4 West / Bed 12",
        "diagnosis": {
            "primary": ("Lobar pneumonia, right lower lobe", "J18.1"),
            "secondary": [("Type 2 diabetes mellitus without complications", "E11.9"), ("Essential (primary) hypertension", "I10")],
        },
        "procedures": [
            {"name": "Flexible bronchoscopy with bronchoalveolar lavage", "date": date(2025, 5, 5), "code": "31624"},
            {"name": "Ultrasound-guided right thoracentesis", "date": date(2025, 5, 6), "code": "32555"},
        ],
        "course": [
            ("Admitted with fever, productive cough and pleuritic chest pain; X-ray showed right lower lobe consolidation "
            "with a small effusion. Treated with IV ceftriaxone and azithromycin, switched to oral therapy on day 5."),
        ],
        "status_label": "Improved",
        "condition_note": "afebrile for 72 hours, ambulating independently, SpO2 96% on room air.",
        "discharge_meds": [
            "Amoxicillin/clavulanate 875/125 mg orally twice daily for 5 more days",
            "Metformin 1000 mg orally twice daily (home medication, continued)",
            "Amlodipine 5 mg orally once daily (home medication, continued)",
        ],
        "instructions": "Complete the full course of antibiotics. Rest at home and increase activity gradually. "
        "Check blood glucose twice daily and keep a log. Return to the Emergency Department for fever above 38.5 C, "
        "shortness of breath or chest pain.",
        "follow_up_date": date(2025, 5, 20),
        "follow_up_where": "Pulmonology Clinic, Building B, 2nd floor (repeat chest X-ray before the visit)",
        "attending_physician": "Dr. Robert Lindqvist",
        "attending_title": "Attending Physician, Department of Pulmonology",
        "discharge_status": "improved",
    }
    return SampleDoc(
        doc_id="core_d01_en", set="core", doc_type="discharge_summary", language="en", variant="clean",
        template="discharge_en.html.j2", data=data,
        notes="EN discharge summary: principal + 2 secondary ICD-10 diagnoses, 2 dated procedures with codes, attending physician, follow-up date. Discharge medications are not procedures.",
    )  # fmt: skip


def _d02() -> SampleDoc:
    data = {
        "facility": {
            "authority": "SỞ Y TẾ TỈNH ĐỒNG NAI",
            "name": "Bệnh viện Đa khoa Phước Hưng Long",
            "address": "36 Đường Võ Thị Sáu, P. Quyết Thắng, TP. Biên Hòa",
        },
        "department": "Ngoại tổng hợp",
        "record_no": "2403/0917",
        "medical_id": "75-0281-24-0917",
        "patient_name": "Trần Văn Hùng",
        "dob": "08/11/1989",
        "sex": "Nam",
        "ethnicity": "Kinh",
        "occupation": "Công nhân",
        "insurance_card": "DN4757512098811",
        "patient_address": "Tổ 5, KP 3, Phường Tân Hiệp, TP. Biên Hòa, Đồng Nai",
        "admission": datetime(2024, 3, 12, 8, 30),
        "discharge": datetime(2024, 3, 17, 9, 0),
        "diagnosis": {"primary": ("Viêm ruột thừa cấp", "K35.8"), "secondary": []},
        "procedures": [{"name": "Phẫu thuật nội soi cắt ruột thừa", "date": None, "code": None}],
        "instructions": "Kiêng vận động mạnh trong 4 tuần. Thay băng vết mổ cách ngày tại cơ sở y tế gần nhất. "
        "Tái khám sau 7 ngày hoặc khi có dấu hiệu bất thường.",
        "sick_leave_from": date(2024, 3, 17),
        "sick_leave_to": date(2024, 3, 31),
        "director": "Nguyễn Hữu Phước",
        "attending_physician": "Lê Minh Khoa",
        "stamp": {"top": "BỆNH VIỆN ĐA KHOA PHƯỚC HƯNG LONG", "bottom": "SỞ Y TẾ ĐỒNG NAI", "center": ["BIÊN HÒA"]},
    }
    return SampleDoc(
        doc_id="core_d02_vi_scan", set="core", doc_type="discharge_summary", language="vi", variant="scan",
        template="discharge_vn_form.html.j2", data=data, degrade="scan",
        notes="VN 'Giấy ra viện' (MOH form 01/BV-01) as a scan: 'Vào viện lúc ... ngày 12 tháng 03 năm 2024', ICD in 'K35.8 - Viêm ruột thừa cấp', single procedure without a date, 'Ghi chú' = instructions, home-rest dates = sick leave, Trưởng khoa = attending.",
    )  # fmt: skip


def _d03() -> SampleDoc:
    data = {
        "facility": {
            "name": "Harbourview Lakes Hospital",
            "address": "42 Quayside Terrace, Port Wellam, Auckland 1010, New Zealand",
            "phone": "+64 9 555 0261",
            "logo": {"kind": "hex"},
        },
        "accent": "#1e6b73",
        "title": "INPATIENT DISCHARGE SUMMARY",
        "date_fmt": "%d %b %Y",
        "patient_name": "James O'Connell",
        "mrn": "HLH-552019",
        "dob": "02 Feb 1990",
        "sex": "Male",
        "admission": date(2026, 1, 14),
        "discharge": date(2026, 1, 16),
        "department": "General Medicine",
        "ward": "Ward 6 / Bed 3",
        "diagnosis": {
            "primary": ("Acute gastroenteritis, presumed infectious", "A09"),
            "secondary": [("Dehydration, moderate", "E86.0")],
        },
        "procedures": [],
        "no_procedures_text": "None. Conservative management only (intravenous fluids, antiemetics, oral rehydration).",
        "course": [
            ("Presented with 2 days of vomiting and watery diarrhoea after a family gathering. Clinically dehydrated with mild "
            "acute kidney injury (creatinine 128 umol/L), which normalised after 24 hours of intravenous crystalloid."),
            "Stool culture negative. Tolerating oral diet on day 2.",
        ],
        "status_label": "Improved",
        "condition_note": "tolerating diet, normal urine output.",
        "discharge_meds": ["Oral rehydration salts, 1 sachet after each loose stool", "Ondansetron 4 mg orally every 8 hours as needed for nausea (6 tablets)"],
        "instructions": "Drink at least 2 litres of fluid a day and use oral rehydration salts after each loose stool. "
        "Wash hands carefully and avoid preparing food for others for 48 hours after symptoms stop. "
        "See your GP if vomiting persists for more than 24 hours or if you notice blood in the stool.",
        "follow_up_date": None,
        "follow_up_where": "Follow-up with your general practitioner within one week if symptoms persist.",
        "attending_physician": "Dr. Aisha Rahman",
        "attending_title": "Consultant Physician, General Medicine",
        "discharge_status": "improved",
        "sig_variant": 0,
    }
    return SampleDoc(
        doc_id="core_d03_en_conservative", set="core", doc_type="discharge_summary", language="en", variant="clean",
        template="discharge_en.html.j2", data=data,
        notes="EN discharge with conservative treatment only: procedures section printed as 'None' -> []; discharge_status improved; no follow-up date.",
    )  # fmt: skip


# --------------------------------------------------------------------------------------------
# lab reports
# --------------------------------------------------------------------------------------------
def t(name: str, result: str, unit: str | None, rng: str | None, flag: str | None = None) -> dict[str, Any]:
    return {"test_name": name, "result": result, "unit": unit, "reference_range": rng, "printed_flag": flag}


def _l01() -> SampleDoc:
    data = {
        "facility": {
            "name": "Clearwater Diagnostics Laboratory",
            "address": "Unit 5, 18 Fenwick Science Park, Clearwater Bay Road, Hong Kong",
            "phone": "+852 2555 0193",
            "email": "results@clearwater-dx.example",
            "accreditation": "Accredited Laboratory No. HOKLAS-L-0921 (fictional)",
            "logo": {"kind": "hex"},
        },
        "accent": "#5b2a86",
        "date_fmt": "%d-%b-%Y",
        "lab_no": "CDL25092214",
        "patient_name": "Emily Tran",
        "dob": "23-Apr-1987",
        "sex": "F",
        "patient_id": "CW-771045",
        "specimen": "Whole blood (EDTA), serum",
        "collection_date": date(2025, 9, 21),
        "collection_time": "08:10",
        "date": date(2025, 9, 22),
        "ordering_physician": "Dr. Michael Grant",
        "referrer_clinic": "Kowloon Bay Family Practice",
        "has_flag_column": True,
        "sections": [
            {"name": "HAEMATOLOGY - COMPLETE BLOOD COUNT", "tests": [
                t("Haemoglobin", "11.2", "g/dL", "12.0 - 15.5", "L"),
                t("Haematocrit", "34.1", "%", "36.0 - 46.0", "L"),
                t("White Blood Cell Count", "12.8", "x10^9/L", "4.0 - 11.0", "H"),
                t("Platelet Count", "265", "x10^9/L", "150 - 450"),
                t("MCV", "82", "fL", "80 - 100"),
            ]},
            {"name": "CLINICAL BIOCHEMISTRY", "tests": [
                t("Glucose, fasting", "6.4", "mmol/L", "3.9 - 5.6", "H"),
                t("Creatinine", "78", "umol/L", "45 - 90"),
                t("ALT (SGPT)", "52", "U/L", "7 - 35", "H"),
                t("Sodium", "139", "mmol/L", "135 - 145"),
                t("Total Cholesterol", "5.1", "mmol/L", "< 5.2"),
            ]},
        ],
        "technologist": "Kelvin Lau",
        "pathologist": "Dr. Sophia Cheung",
        "stamp": {"top": "CLEARWATER DIAGNOSTICS", "bottom": "VERIFIED", "center": ["22 SEP 2025"]},
    }
    return SampleDoc(
        doc_id="core_l01_en", set="core", doc_type="lab_report", language="en", variant="clean",
        template="lab_en.html.j2", data=data,
        notes="EN CBC + biochemistry with a printed H/L flag column; blank flag cells (normal) are not printed flags, so truth flag is derived (flag_source 'derived').",
    )  # fmt: skip


def _l02() -> SampleDoc:
    data = {
        "facility": {
            "name": "Trung tâm Xét nghiệm Y khoa Bình An Phước",
            "address": "102 Đường Trần Hưng Đạo, Phường Cái Khế, Quận Ninh Kiều, TP. Cần Thơ",
            "phone": "0292 3811 456",
            "tax_code": "1801654329",
            "accreditation": "ISO 15189:2022 (mô phỏng)",
            "website": "ketqua.binhanphuoc-lab.example",
            "logo": {"kind": "leaf"},
        },
        "lab_no": "XN-250605-0331",
        "subtitle": "Sinh hóa máu - Miễn dịch",
        "patient_name": "Lê Thị Mai Anh",
        "dob": "1972",
        "sex": "Nữ",
        "patient_address": "Phường An Hòa, Quận Ninh Kiều, TP. Cần Thơ",
        "insurance_card": "HC4929200177120",
        "ordering_physician": "Đặng Văn Toàn",
        "ward": "Phòng khám Nội 2",
        "diagnosis_text": "Theo dõi rối loạn chuyển hóa",
        "specimen": "Máu tĩnh mạch",
        "collection_time": "07:15",
        "collection_date": date(2025, 6, 5),
        "date": datetime(2025, 6, 5, 10, 40),
        "sections": [
            {"name": "SINH HÓA MÁU", "tests": [
                t("Glucose", "7.2", "mmol/L", "3.9 - 6.4"),
                t("Ure", "5.1", "mmol/L", "2.5 - 7.5"),
                t("Creatinin", "102", "µmol/L", "44 - 80"),
                t("AST (GOT)", "28", "U/L", "≤ 37"),
                t("ALT (GPT)", "45", "U/L", "≤ 40"),
                t("Cholesterol toàn phần", "4.8", "mmol/L", "3.9 - 5.2"),
                t("Triglycerid", "2.9", "mmol/L", "0.46 - 1.88"),
            ]},
            {"name": "MIỄN DỊCH", "tests": [
                t("HBsAg (test nhanh)", "Âm tính", None, "Âm tính"),
            ]},
        ],
        "technologist": "Ngô Thị Bích Trâm",
        "pathologist": "Huỳnh Quốc Việt",
        "stamp": {"top": "TT XÉT NGHIỆM Y KHOA BÌNH AN PHƯỚC", "bottom": "CẦN THƠ", "center": ["ĐÃ DUYỆT"]},
    }
    return SampleDoc(
        doc_id="core_l02_vi", set="core", doc_type="lab_report", language="vi", variant="clean",
        template="lab_vn.html.j2", data=data,
        notes="VN lab report without a flag column: every numeric flag is derived from result vs range (incl. '≤ 40' ranges); the qualitative HBsAg row gets flag null.",
    )  # fmt: skip


# --------------------------------------------------------------------------------------------
# prescriptions
# --------------------------------------------------------------------------------------------
def _p01() -> SampleDoc:
    data = {
        "facility": {
            "name": "Willowmere Medical Centre",
            "address": "7 Thornbury Lane, Willowmere, Manchester M21 4QX",
            "phone": "0161 555 0177",
            "logo": {"kind": "initials", "initials": "WM"},
        },
        "accent": "#1f6f5c",
        "doctor_name": "Dr. Hannah Okafor",
        "doctor_quals": "MBChB, MRCGP - General Practitioner",
        "doctor_reg": "GMC Reg. No. 7740219 (fictional)",
        "patient_name": "Liam Patel",
        "dob": "19 Oct 1994 (31 y)",
        "patient_id": "WMC-30318",
        "patient_address": "14 Birchfield Road, Manchester M20 3LP",
        "allergies": "No known drug allergies",
        "date": date(2026, 3, 11),
        "date_fmt": "%d %B %Y",
        "diagnosis": {"primary": ("Acute bacterial sinusitis", "J01.90"), "secondary": []},
        "medications": [
            {"name": "Amoxicillin 500 mg capsule", "dosage": "1 capsule", "frequency": "three times daily", "duration": "7 days", "quantity": 21},
            {"name": "Ibuprofen 400 mg tablet", "dosage": "1 tablet", "frequency": "every 8 hours as needed for pain", "duration": "5 days", "quantity": 15},
            {"name": "Fluticasone propionate 50 mcg nasal spray", "dosage": "2 sprays each nostril", "frequency": "once daily", "duration": "14 days", "quantity": 1},
        ],
        "note": "Take ibuprofen with food. Return if symptoms worsen or persist beyond 10 days.",
        "rx_no": "WM-RX-260311-044",
    }
    return SampleDoc(
        doc_id="core_p01_en", set="core", doc_type="prescription", language="en", variant="clean",
        template="rx_en.html.j2", data=data,
        notes="Typed EN prescription, 3 medications with '#21' style quantities, diagnosis with ICD-10 in parentheses.",
    )  # fmt: skip


def _p02() -> SampleDoc:
    data = {
        "facility": {
            "authority": "SỞ Y TẾ HÀ NỘI",
            "name": "Phòng khám Đa khoa Vạn Lộc Xanh",
            "address": "58 Phố Hoàng Quốc Việt, Phường Nghĩa Đô, Quận Cầu Giấy, Hà Nội",
            "phone": "024 3755 2810",
            "logo": {"kind": "cross", "color": "#0f6a3d"},
        },
        "rx_no": "01845VLX-260520-118",
        "patient_id": "BN2605200118",
        "patient_name": "Phạm Gia Huy",
        "dob": "03/07/2001",
        "sex": "Nam",
        "patient_address": "Ngõ 120 Trần Duy Hưng, Phường Trung Hòa, Quận Cầu Giấy, Hà Nội",
        "insurance_card": "HN4010120998877",
        "diagnosis": {"primary": ("Viêm họng cấp", "J02.9"), "secondary": []},
        "medications": [
            {"name": "Cefuroxim 500mg (Zinnat)", "unit": "Viên", "quantity": 14, "dosage": "1 viên", "frequency": "Ngày uống 2 lần", "note": "sau ăn sáng và tối", "duration": "7 ngày"},
            {"name": "Paracetamol 500mg (Panadol)", "unit": "Viên", "quantity": 10, "dosage": "1 viên", "frequency": "Ngày uống 3 lần", "note": "khi sốt trên 38.5 độ hoặc đau", "duration": None},
            {"name": "Alpha chymotrypsin 4200 IU", "unit": "Viên", "quantity": 20, "dosage": "2 viên", "frequency": "Ngày ngậm 2 lần", "note": None, "duration": "5 ngày"},
        ],
        "advice": "Uống nhiều nước ấm, súc họng bằng nước muối sinh lý. Hạn chế đồ lạnh, cay nóng.",
        "revisit_text": "sau 7 ngày hoặc khi có dấu hiệu bất thường",
        "date": date(2026, 5, 20),
        "doctor_name": "Võ Thị Thanh Hà",
    }
    return SampleDoc(
        doc_id="core_p02_vi", set="core", doc_type="prescription", language="vi", variant="clean",
        template="rx_vn.html.j2", data=data,
        notes="VN 'ĐƠN THUỐC': usage on the line below each drug ('Ngày uống 2 lần, mỗi lần 1 viên'), 'SL: 14 Viên' quantities, diagnosis 'J02.9 - Viêm họng cấp'; one drug has no duration.",
    )  # fmt: skip


def core_docs() -> list[SampleDoc]:
    return [_r01(), _r02(), _r03(), _d01(), _d02(), _d03(), _l01(), _l02(), _p01(), _p02()]
