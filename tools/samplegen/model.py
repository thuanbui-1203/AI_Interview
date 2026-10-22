"""Sample definition model, formatting filters and truth builders.

Conventions that keep render and truth in lock-step:

- A printed date is either a ``datetime.date`` (truth = ISO) or a ``str`` that is printed
  verbatim because it is not a real date (truth = ``None``, the ``DATE_INVALID`` case).
- Money is a ``float`` in the data; templates format it per locale, truth keeps the float.
- ``None`` in the data means "not printed" (or printed as a blank line) and becomes an explicit
  ``null`` in the truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from medextract.validation.parsing import derive_flag as _pipeline_derive_flag

Variant = Literal["clean", "scan", "photo", "handwriting"]
Degrade = Literal["none", "scan", "heavy_scan", "photo"]
PrintedDate = date | str | None


@dataclass(frozen=True)
class SampleDoc:
    doc_id: str
    set: Literal["core", "stress"]
    doc_type: str | None  # None = out-of-scope (non-medical) document
    language: Literal["en", "vi", "mixed"]
    variant: Variant
    template: str
    data: dict[str, Any]
    degrade: Degrade = "none"
    expected_issues: tuple[str, ...] = ()
    low_confidence_fields: tuple[str, ...] = ()
    notes: str = ""
    pages: int = 1

    @property
    def file_name(self) -> str:
        return "document.pdf" if self.degrade == "none" else "document.png"


# --------------------------------------------------------------------------------------------
# formatting (registered as Jinja filters; truth never goes through these)
# --------------------------------------------------------------------------------------------
def fmt_money(value: float | None, style: str = "en2") -> str:
    """'en2' -> 1,234.50 | 'en0' -> 1,234 | 'vn' -> 1.250.000 (VND has no decimals)."""
    if value is None:
        return ""
    sign = "-" if value < 0 else ""
    v = abs(value)
    if style == "vn":
        return sign + f"{round(v):,}".replace(",", ".")
    if style == "en0":
        return sign + f"{round(v):,}"
    return sign + f"{v:,.2f}"


def fmt_date(value: PrintedDate, fmt: str = "%d %b %Y") -> str:
    """Format a printable date; invalid raw strings are printed exactly as given."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.strftime(fmt)


def fmt_qty(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


# --------------------------------------------------------------------------------------------
# Vietnamese amount in words
# --------------------------------------------------------------------------------------------
_DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
_SCALES = ["", "nghìn", "triệu", "tỷ"]


def _read_group(n: int, leading: bool) -> list[str]:
    h, t, u = n // 100, (n // 10) % 10, n % 10
    words: list[str] = []
    if h or not leading:
        words += [_DIGITS[h], "trăm"]
    if t == 0 and u:
        if h or not leading:
            words.append("lẻ")
    elif t == 1:
        words.append("mười")
    elif t > 1:
        words += [_DIGITS[t], "mươi"]
    if u:
        if u == 1 and t > 1:
            words.append("mốt")
        elif u == 5 and t >= 1:
            words.append("lăm")
        else:
            words.append(_DIGITS[u])
    return words


def vn_amount_in_words(amount: int) -> str:
    """Read an integer VND amount the way Vietnamese invoices print 'Bằng chữ'."""
    # Negative input would never leave the loop below (-1 // 1000 == -1); scales stop at 'tỷ'.
    if not 0 <= amount < 1000 ** len(_SCALES):
        raise ValueError(f"amount out of range for words: {amount}")
    if amount == 0:
        return "Không đồng"
    groups: list[int] = []
    while amount:
        groups.append(amount % 1000)
        amount //= 1000
    words: list[str] = []
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if g == 0:
            continue
        words += _read_group(g, leading=(i == len(groups) - 1))
        if _SCALES[i]:
            words.append(_SCALES[i])
    text = " ".join(words) + " đồng"
    return text[0].upper() + text[1:]


# --------------------------------------------------------------------------------------------
# truth helpers
# --------------------------------------------------------------------------------------------
def collapse(text: str | None) -> str | None:
    return " ".join(text.split()) if text is not None else None


def iso(value: PrintedDate) -> str | None:
    """Printed date -> ISO; an invalid printed string (e.g. '31/02/2024') -> None."""
    if value is None or isinstance(value, str):
        return None
    if isinstance(value, datetime):
        value = value.date()
    return value.isoformat()


def iso_or_raw(value: PrintedDate) -> str | None:
    """Procedure-row dates: ISO when parseable, else the raw text (contract)."""
    if isinstance(value, str):
        return value
    return iso(value)


_FLAG_MAP = {"H": "high", "L": "low", "N": "normal", "HIGH": "high", "LOW": "low", "↑": "high", "↓": "low"}


# Truth flags must be derived exactly like the pipeline derives them, so reuse its function.
derive_flag = _pipeline_derive_flag


def _diagnosis(dx: dict[str, Any] | None) -> dict[str, Any] | None:
    if dx is None:
        return None

    def entry(e: tuple[str, str | None]) -> dict[str, Any]:
        return {"description": collapse(e[0]), "icd10_code": e[1]}

    return {
        "primary": entry(dx["primary"]) if dx.get("primary") else None,
        "secondary": [entry(e) for e in dx.get("secondary", [])],
    }


# --------------------------------------------------------------------------------------------
# truth builders, one per document type
# --------------------------------------------------------------------------------------------
def _receipt(d: dict[str, Any]) -> dict[str, Any]:
    items = d.get("items")
    return {
        "currency": d.get("currency"),
        "hospital_name": collapse(d["facility"]["name"]),
        "patient_name": collapse(d.get("patient_name")),
        "date": iso(d.get("date")),
        "items": None
        if items is None
        else [
            {
                "description": collapse(i["description"]),
                "quantity": _f(i.get("quantity")),
                "unit_price": _f(i.get("unit_price")),
                "total": _f(i.get("total")),
                "category": i.get("category"),
            }
            for i in items
        ],
        "grand_total": _f(d.get("grand_total")),
        "payment_method": d.get("payment_method"),
        "invoice_number": d.get("invoice_number"),
        "subtotal": _f(d.get("subtotal")),
        "discount_total": _f(d.get("discount_total")),
        "tax_amount": _f(d.get("tax_amount")),
        "insurance_covered_amount": _f(d.get("insurance_covered_amount")),
        "patient_payable": _f(d.get("patient_payable")),
        "amount_in_words": collapse(d.get("amount_in_words")),
    }


def _discharge(d: dict[str, Any]) -> dict[str, Any]:
    procs = d.get("procedures")
    return {
        "hospital_name": collapse(d["facility"]["name"]),
        "patient_name": collapse(d.get("patient_name")),
        "admission_date": iso(d.get("admission")),
        "discharge_date": iso(d.get("discharge")),
        "diagnosis": _diagnosis(d.get("diagnosis")),
        "procedures_performed": None
        if procs is None
        else [{"name": collapse(p["name"]), "date": iso_or_raw(p.get("date")), "code": p.get("code")} for p in procs],
        "attending_physician": collapse(d.get("attending_physician")),
        "discharge_instructions": collapse(d.get("instructions")),
        "department": collapse(d.get("department")),
        "discharge_status": d.get("discharge_status"),
        "follow_up_date": iso(d.get("follow_up_date")),
        "sick_leave_from": iso(d.get("sick_leave_from")),
        "sick_leave_to": iso(d.get("sick_leave_to")),
    }


def lab_row_truth(t: dict[str, Any]) -> dict[str, Any]:
    """Printed flag wins (even if wrong); otherwise derive, as the normalizer must."""
    printed = (t.get("printed_flag") or "").strip().upper()
    if printed:
        flag, source = _FLAG_MAP[printed], "printed"
    else:
        flag = derive_flag(t.get("result"), t.get("reference_range"))
        source = "derived" if flag is not None else None
    return {
        "test_name": collapse(t["test_name"]),
        "result": t.get("result"),
        "unit": t.get("unit"),
        "reference_range": t.get("reference_range"),
        "flag": flag,
        "flag_source": source,
    }


def _lab(d: dict[str, Any]) -> dict[str, Any]:
    tests = [t for section in d["sections"] for t in section["tests"]]
    return {
        "lab_name": collapse(d["facility"]["name"]),
        "patient_name": collapse(d.get("patient_name")),
        "date": iso(d.get("date")),
        "tests": [lab_row_truth(t) for t in tests],
        "ordering_physician": collapse(d.get("ordering_physician")),
        "collection_date": iso(d.get("collection_date")),
    }


def _prescription(d: dict[str, Any]) -> dict[str, Any]:
    def cell(m: dict[str, Any], name: str) -> Any:
        return None if name in m.get("smudged", ()) else m.get(name)

    return {
        "doctor_name": collapse(d.get("doctor_name")),
        "patient_name": collapse(d.get("patient_name")),
        "date": iso(d.get("date")),
        "medications": [
            {
                "name": collapse(cell(m, "name")),
                "dosage": collapse(cell(m, "dosage")),
                "frequency": collapse(cell(m, "frequency")),
                "duration": collapse(cell(m, "duration")),
                "quantity": _f(cell(m, "quantity")),
            }
            for m in d["medications"]
        ],
        "facility_name": collapse(d["facility"]["name"]),
        "diagnosis": _diagnosis(d.get("diagnosis")),
    }


def _f(v: float | None) -> float | None:
    return None if v is None else float(v)


TRUTH_BUILDERS = {
    "receipt": _receipt,
    "discharge_summary": _discharge,
    "lab_report": _lab,
    "prescription": _prescription,
}


def build_fields(doc: SampleDoc) -> dict[str, Any]:
    """Truth ``fields`` for a doc; empty for out-of-scope (non-medical) documents."""
    if doc.doc_type is None:
        return {}
    return TRUTH_BUILDERS[doc.doc_type](doc.data)


def build_truth(doc: SampleDoc) -> dict[str, Any]:
    return {
        "doc_id": doc.doc_id,
        "set": doc.set,
        "document_type": doc.doc_type,
        "language": doc.language,
        "file": doc.file_name,
        "variant": doc.variant,
        "pages": doc.pages,
        "fields": build_fields(doc),
        "expected_issues": list(doc.expected_issues),
        "low_confidence_fields": list(doc.low_confidence_fields),
        "notes": doc.notes,
    }

