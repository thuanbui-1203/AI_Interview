"""Laboratory test report (VN: Phiếu kết quả xét nghiệm)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from medextract.core.fields import Bucket, DateField, ListField, Row, TextField
from medextract.core.registry import DocTypeSpec
from medextract.doctypes._common import ExtractionBase


class LabTest(Row):
    test_name: str | None
    result: str | None = Field(description="Result exactly as printed, as text ('6.8', 'Negative', '<0.5', '1+').")
    unit: str | None
    reference_range: str | None = Field(description="Reference range exactly as printed ('3.5 - 5.0', '<5.0', 'Negative').")
    flag: Literal["normal", "high", "low"] | None = Field(
        description="ONLY the flag printed on the document (H/↑/High -> high, L/↓/Low -> low, N/Normal -> normal). null if no flag is printed for this row - do not compute it yourself."
    )
    confidence: Bucket


class LabReportExtraction(ExtractionBase):
    lab_name: TextField = Field(description="The laboratory or hospital lab issuing the report.")
    patient_name: TextField
    date: DateField = Field(description="Report / result date. If only one date is printed, use it.")
    tests: ListField[LabTest]
    ordering_physician: TextField = Field(description="Requesting physician (VN: 'Bác sĩ chỉ định').")
    collection_date: DateField = Field(description="Specimen collection date if printed separately (VN: 'Ngày lấy mẫu').")


HINTS = """\
LAB REPORT / PHIẾU KẾT QUẢ XÉT NGHIỆM
- tests: one row per test result line, in order. Section headers (e.g. 'HEMATOLOGY', 'Huyết học') are not tests.
- result, unit and reference_range are copied as printed text. Keep comparison signs and qualitative results.
- flag: transcribe only a printed flag. Even if a value looks out of range, leave flag null when nothing is printed (code will derive it).
- date: the report/result date ('Ngày trả kết quả'); collection_date is the sampling date ('Ngày lấy mẫu') when both are printed."""

CUES = "Table of test names with results, units and reference ranges; words like laboratory, lab report, xét nghiệm, kết quả."

SPEC = DocTypeSpec(
    name="lab_report",
    description="Laboratory report listing test results with units and reference ranges.",
    schema=LabReportExtraction,
    required_fields=("lab_name", "patient_name", "date", "tests"),
    prompt_hints=HINTS,
    classifier_cues=CUES,
)
