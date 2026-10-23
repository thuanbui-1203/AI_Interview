"""Prescription (VN: Đơn thuốc, Toa thuốc)."""

from __future__ import annotations

from pydantic import Field

from medextract.core.fields import Bucket, DateField, DiagnosisField, ListField, Row, TextField
from medextract.core.registry import DocTypeSpec
from medextract.doctypes._common import ExtractionBase


class Medication(Row):
    name: str | None = Field(description="Drug name as printed (brand and/or generic, e.g. 'Amoxicillin 500mg (Augmentin)').")
    dosage: str | None = Field(
        description="Amount taken per administration, as printed ('1 viên', '1 capsule', '2 puffs', '5 ml'). The drug strength ('500mg') belongs to name, not here - unless the dose is only given as an amount like '250 mg'."
    )
    frequency: str | None = Field(
        description="How often only, as printed ('Ngày uống 2 lần', 'three times daily', 'bid'). Exclude dose, timing and other advice ('sau ăn')."
    )
    duration: str | None = Field(description="As printed ('7 days', 'x5d', '5 ngày').")
    quantity: float | None = Field(description="Total quantity dispensed as a number ('#21', 'SL: 21 viên' -> 21).")
    confidence: Bucket


class PrescriptionExtraction(ExtractionBase):
    doctor_name: TextField = Field(description="Prescribing doctor's printed name (VN: 'Bác sĩ khám bệnh', 'Y bác sĩ').")
    patient_name: TextField
    date: DateField = Field(description="Prescription date.")
    medications: ListField[Medication]
    facility_name: TextField = Field(description="Clinic / hospital issuing the prescription.")
    diagnosis: DiagnosisField


HINTS = """\
PRESCRIPTION / ĐƠN THUỐC
- medications: one row per drug. Keep dosage, frequency and duration as printed text; quantity is the total amount dispensed as a number.
  Vietnamese prescriptions often put usage on the next line ('Ngày uống 2 lần, mỗi lần 1 viên, sau ăn') - attach it to the drug
  above and split it: frequency 'Ngày uống 2 lần', dosage '1 viên' (timing advice like 'sau ăn' goes nowhere).
- If one part of a row is unreadable (smudged), set that cell to null and lower the row confidence; never guess a dose.
- doctor_name: a printed name only (not an unreadable signature)."""

CUES = "List of medicines with dose, frequency and duration signed by a doctor; words like prescription, Rx, đơn thuốc, toa thuốc."

SPEC = DocTypeSpec(
    name="prescription",
    description="Doctor's prescription listing medications with dosage, frequency, duration and quantity.",
    schema=PrescriptionExtraction,
    required_fields=("doctor_name", "patient_name", "date", "medications"),
    prompt_hints=HINTS,
    classifier_cues=CUES,
)
