"""Discharge summary (VN: Giấy ra viện, Tóm tắt hồ sơ bệnh án, Giấy chứng nhận ra viện)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from medextract.core.fields import Bucket, ChoiceField, DateField, DiagnosisField, ListField, Row, TextField
from medextract.core.registry import DocTypeSpec
from medextract.doctypes._common import ExtractionBase

DischargeStatus = Literal[
    "recovered", "improved", "unchanged", "worse", "transferred", "against_medical_advice", "deceased"
]  # fmt: skip


class Procedure(Row):
    name: str | None
    date: str | None = Field(description="Procedure date exactly as printed; null if not printed.")
    code: str | None = Field(description="Procedure code only if printed.")
    confidence: Bucket


class DischargeSummaryExtraction(ExtractionBase):
    hospital_name: TextField
    patient_name: TextField
    admission_date: DateField = Field(description="VN: 'Ngày vào viện', 'Vào viện lúc ... ngày ...'.")
    discharge_date: DateField = Field(description="VN: 'Ngày ra viện', 'Ra viện lúc ... ngày ...'.")
    diagnosis: DiagnosisField
    procedures_performed: ListField[Procedure] = Field(
        description="Surgeries / procedures performed (VN: 'Phương pháp điều trị', 'Phẫu thuật', 'Thủ thuật'). Conservative/medical treatment only ('Điều trị nội khoa') -> empty rows with status present."
    )
    attending_physician: TextField = Field(
        description="Treating / attending physician's name (VN: 'Bác sĩ điều trị'; if only the signer is named, e.g. 'Trưởng khoa', use that name). Only a printed name - never a signature you cannot read."
    )
    discharge_instructions: TextField = Field(description="Advice at discharge, verbatim (VN: 'Lời dặn', 'Ghi chú', 'Hướng điều trị tiếp').")
    department: TextField = Field(description="Ward / department (VN: 'Khoa').")
    discharge_status: ChoiceField[DischargeStatus] = Field(
        description="Condition at discharge if printed (VN: 'Tình trạng ra viện': Khỏi -> recovered, Đỡ/Giảm -> improved, Không thay đổi -> unchanged, Nặng hơn -> worse, Chuyển viện -> transferred, Xin về -> against_medical_advice, Tử vong -> deceased)."
    )
    follow_up_date: DateField = Field(description="Follow-up appointment date (VN: 'Hẹn tái khám').")
    sick_leave_from: DateField = Field(description="Start of prescribed home rest / sick leave (VN: 'Nghỉ ngơi tại nhà từ ngày').")
    sick_leave_to: DateField = Field(description="End of prescribed home rest / sick leave ('đến ngày').")


HINTS = """\
DISCHARGE SUMMARY / GIẤY RA VIỆN
- admission_date / discharge_date: the hospital stay dates. Vietnamese forms write 'Vào viện lúc 08 giờ 30 phút, ngày 12 tháng 03 năm 2024'
  -> raw is the date part as printed, value 2024-03-12. Vietnamese dates are always day/month/year.
- diagnosis: primary = the diagnosis labelled main/principal ('Chẩn đoán', 'Bệnh chính'); secondary = comorbidities ('Bệnh kèm theo').
  If a code is printed next to the text ('K35.8 - Viêm ruột thừa cấp'), split it into icd10_code and description. Never add a code that is not printed.
- procedures_performed: one row per named procedure/surgery. Do not list medications or lab tests as procedures.
- attending_physician: a printed name only. If the signature area has no legible printed name -> not_found.
- A multi-page summary: combine information from all pages; do not duplicate a diagnosis that appears on several pages."""

CUES = "Hospital stay with admission and discharge dates, diagnoses, treatment given; words like discharge summary, giấy ra viện, ra viện, vào viện."

SPEC = DocTypeSpec(
    name="discharge_summary",
    description="Hospital discharge summary / discharge certificate describing an inpatient stay, diagnoses and treatment.",
    schema=DischargeSummaryExtraction,
    required_fields=(
        "hospital_name",
        "patient_name",
        "admission_date",
        "discharge_date",
        "diagnosis",
        "procedures_performed",
        "attending_physician",
        "discharge_instructions",
    ),
    prompt_hints=HINTS,
    classifier_cues=CUES,
)
