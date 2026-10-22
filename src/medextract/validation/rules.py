"""Generic validation rules, parameterized by field names.

Rules read the *public* values defined in docs/OUTPUT_CONTRACT.md (ISO date strings, floats,
row dicts), plus ``raw``/``status`` where the value alone cannot tell "absent" from "invalid".
Every rule is defensive: a missing field, a null cell or an unexpected type means "nothing to
check", never an exception - the validator must survive whatever the model returns.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from medextract.core.models import FieldResult, Severity, ValidationIssue
from medextract.validation.base import Rule, RuleContext
from medextract.validation.numwords import words_to_number
from medextract.validation.parsing import (
    ICD10_RE,
    derive_flag,
    normalize_icd10,
    parse_date,
)

Fields = Mapping[str, FieldResult]

# --------------------------------------------------------------------------------------------
# Value accessors (tolerant of nulls and wrong types)
# --------------------------------------------------------------------------------------------


def _value(fields: Fields, name: str) -> Any:
    fr = fields.get(name)
    return fr.value if fr is not None else None


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v) if math.isfinite(v) else None
    if isinstance(v, str):
        try:
            f = float(v.strip())
        except ValueError:
            return None
        return f if math.isfinite(f) else None
    return None


def _iso(v: Any) -> date | None:
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v.strip())
        except ValueError:
            return None
    return None


def _rows(fields: Fields, name: str) -> list[dict[str, Any]]:
    """Row dicts of a list field; ``[]`` for null/absent tables, non-dict rows skipped."""
    v = _value(fields, name)
    if not isinstance(v, list):
        return []
    return [r if isinstance(r, dict) else {} for r in v]  # keep indices stable for paths


def _is_discount(row: Mapping[str, Any]) -> bool:
    return row.get("category") == "discount"


def _issue(code: str, severity: Severity, fields: list[str], message: str, /, **details: Any) -> ValidationIssue:
    return ValidationIssue(code=code, severity=severity, fields=fields, message=message, details=details)


def _fmt(x: float) -> str:
    return f"{x:,.2f}".rstrip("0").rstrip(".")


# --------------------------------------------------------------------------------------------
# Generic rules
# --------------------------------------------------------------------------------------------


class RequiredFields:
    """A brief-required field came back null. Correct behaviour, but the assessor must know.

    ``[]`` counts as present: the contract uses it for "section printed, no entries".
    """

    code = "REQUIRED_FIELD_MISSING"

    def __init__(self, field_names: Sequence[str]) -> None:
        self.field_names = tuple(field_names)

    def check(self, fields: Fields, ctx: RuleContext) -> list[ValidationIssue]:
        out = []
        for name in self.field_names:
            v = _value(fields, name)
            if v is None or (isinstance(v, str) and not v.strip()):
                out.append(_issue(self.code, "warning", [name], f"Required field '{name}' was not found on the document."))
        return out


class DateValid:
    """A printed date that is not a real day (31/02) or has an implausible year."""

    code = "DATE_INVALID"

    def __init__(self, *field_names: str) -> None:
        self.field_names = field_names

    def check(self, fields: Fields, ctx: RuleContext) -> list[ValidationIssue]:
        out = []
        for name in self.field_names:
            fr = fields.get(name)
            if fr is None:
                continue
            problem = self._problem(fr, ctx.today)
            if problem:
                out.append(_issue(self.code, "error", [name], f"'{name}': {problem}", raw=fr.raw, value=fr.value))
        return out

    @staticmethod
    def _problem(fr: FieldResult, today: date) -> str | None:
        if fr.value is None:
            printed = bool(fr.raw and fr.raw.strip()) and fr.status != "not_found"
            return f"printed date {fr.raw!r} is not a valid calendar date" if printed else None
        d = _iso(fr.value)
        if d is None:
            return f"value {fr.value!r} is not an ISO date"
        if not 1900 <= d.year <= today.year + 1:
            return f"year {d.year} is outside 1900..{today.year + 1}"
        return None


class NotFutureDate:
    """Documents cannot be issued in the future; one day of slack covers time zones."""

    code = "DATE_IN_FUTURE"

    def __init__(self, *field_names: str) -> None:
        self.field_names = field_names

    def check(self, fields: Fields, ctx: RuleContext) -> list[ValidationIssue]:
        limit = ctx.today + timedelta(days=1)
        out = []
        for name in self.field_names:
            d = _iso(_value(fields, name))
            if d is not None and d > limit:
                out.append(_issue(self.code, "error", [name], f"'{name}' {d.isoformat()} is in the future.", date=d.isoformat(), today=ctx.today.isoformat()))
        return out


class DateAmbiguousOrder:
    """Day/month order cannot be decided from the printed text (e.g. '05/03/2024' with no locale cue).

    Not part of ``rules_for``: the normalizer may report this itself. Provided for pipelines
    that want it as a rule.
    """

    code = "DATE_AMBIGUOUS_ORDER"

    def __init__(self, *field_names: str) -> None:
        self.field_names = field_names

    def check(self, fields: Fields, ctx: RuleContext) -> list[ValidationIssue]:
        out = []
        for name in self.field_names:
            fr = fields.get(name)
            if fr is None or fr.value is None or not fr.raw:
                continue
            if parse_date(fr.raw, language=ctx.language, today=ctx.today).ambiguous:
                out.append(_issue(self.code, "warning", [name], f"'{name}' {fr.raw!r}: day/month order is ambiguous.", raw=fr.raw))
        return out


class DateOrder:
    """``later`` must not precede ``earlier`` (same day is fine)."""

    def __init__(self, earlier: str, later: str, code: str, severity: Severity = "error") -> None:
        self.earlier, self.later, self.code, self.severity = earlier, later, code, severity

    def check(self, fields: Fields, ctx: RuleContext) -> list[ValidationIssue]:
        a, b = _iso(_value(fields, self.earlier)), _iso(_value(fields, self.later))
        if a is None or b is None or b >= a:
            return []
        msg = f"'{self.later}' ({b.isoformat()}) is before '{self.earlier}' ({a.isoformat()})."
        return [_issue(self.code, self.severity, [self.earlier, self.later], msg, earlier=a.isoformat(), later=b.isoformat())]


# --------------------------------------------------------------------------------------------
# Receipt arithmetic
# --------------------------------------------------------------------------------------------

RECEIPT_MONEY_FIELDS = ("grand_total", "subtotal", "discount_total", "tax_amount", "insurance_covered_amount", "patient_payable")


class PositiveAmounts:
    """Amounts must be positive; discount rows are the one place a negative total is correct.

    ``strictly_positive`` fields must be > 0, the other scalar fields >= 0 (a zero BHYT share
    or zero tax is legitimate).
    """

    code = "AMOUNT_NOT_POSITIVE"

    def __init__(
        self,
        scalar_fields: Sequence[str] = RECEIPT_MONEY_FIELDS,
        *,
        strictly_positive: Sequence[str] = ("grand_total",),
        items_field: str | None = "items",
    ) -> None:
        self.scalar_fields = tuple(scalar_fields)
        self.strictly_positive = frozenset(strictly_positive)
        self.items_field = items_field

    def check(self, fields: Fields, ctx: RuleContext) -> list[ValidationIssue]:
        out = []
        for name in self.scalar_fields:
            v = _num(_value(fields, name))
            if v is None:
                continue
            if v < 0 or (v == 0 and name in self.strictly_positive):
                need = "> 0" if name in self.strictly_positive else ">= 0"
                out.append(_issue(self.code, "error", [name], f"'{name}' is {_fmt(v)}; expected {need}.", value=v))
        if self.items_field:
            for i, row in enumerate(_rows(fields, self.items_field)):
                out.extend(self._check_row(i, row))
        return out

    def _check_row(self, i: int, row: Mapping[str, Any]) -> list[ValidationIssue]:
        path = f"{self.items_field}[{i}]"
        out = []
        total = _num(row.get("total"))
        if _is_discount(row):
            if total is not None and total >= 0:
                out.append(_issue(self.code, "error", [f"{path}.total"], f"Discount row {i} total is {_fmt(total)}; expected < 0.", value=total))
            return out  # discount unit prices may be printed with either sign
        for cell in ("quantity", "unit_price", "total"):
            v = _num(row.get(cell))
            if v is not None and v <= 0:
                out.append(_issue(self.code, "error", [f"{path}.{cell}"], f"Row {i} {cell} is {_fmt(v)}; expected > 0.", value=v))
        return out


def _row_amount(row: Mapping[str, Any]) -> float | None:
    """Printed line total, else qty x unit price; discount rows always count as negative."""
    total = _num(row.get("total"))
    if total is None:
        q, p = _num(row.get("quantity")), _num(row.get("unit_price"))
        total = q * p if q is not None and p is not None else None
    if total is None:
        return None
    return -abs(total) if _is_discount(row) else total


class TotalsMatch:
    """Line items (minus discounts, plus tax) must add up to the printed grand total.

    Discount/tax are applied from the scalar fields only when they are not already present as
    rows, so a document that prints both is not double-counted. A row with neither a total nor
    qty x price makes the sum unknowable, so the rule stays silent rather than guessing.
    """

    code = "TOTAL_MISMATCH"

    def __init__(self, tolerance: float = 0.05, items_field: str = "items") -> None:
        self.tolerance = tolerance
        self.items_field = items_field

    def check(self, fields: Fields, ctx: RuleContext) -> list[ValidationIssue]:
        grand = _num(_value(fields, "grand_total"))
        rows = _rows(fields, self.items_field)
        if grand is None or grand <= 0 or not rows:
            return []
        amounts = [_row_amount(r) for r in rows]
        if any(a is None for a in amounts):
            return []
        computed = sum(a for a in amounts if a is not None)
        tax = _num(_value(fields, "tax_amount"))
        if tax is not None and not any(r.get("category") == "tax" for r in rows):
            computed += tax
        discount = _num(_value(fields, "discount_total"))
        if discount is not None and not any(_is_discount(r) for r in rows):
            computed -= abs(discount)
        diff = computed - grand
        rel = round(abs(diff) / grand, 9)  # rounding keeps an exact 5.0 % from tipping over
        details = {"computed": round(computed, 2), "printed": grand, "rel_diff": rel}
        names = ["grand_total", self.items_field]
        if rel > self.tolerance:
            msg = f"Items sum to {_fmt(computed)} but grand total is {_fmt(grand)} ({rel:.1%} off)."
            return [_issue("TOTAL_MISMATCH", "error", names, msg, **details)]
        if abs(diff) > max(1.0, 0.005 * grand):
            msg = f"Items sum to {_fmt(computed)} vs grand total {_fmt(grand)} (small difference)."
            return [_issue("TOTAL_MINOR_DIFF", "warning", names, msg, **details)]
        return []


class LineArithmetic:
    """quantity x unit_price should equal the printed line total (1 unit or 1 % slack)."""

    code = "LINE_MATH_MISMATCH"

    def __init__(self, items_field: str = "items") -> None:
        self.items_field = items_field

    def check(self, fields: Fields, ctx: RuleContext) -> list[ValidationIssue]:
        out = []
        for i, row in enumerate(_rows(fields, self.items_field)):
            q, p, t = (_num(row.get(k)) for k in ("quantity", "unit_price", "total"))
            if q is None or p is None or t is None:
                continue
            expected = q * p
            if _is_discount(row):  # sign conventions vary on discount rows; compare magnitudes
                expected, t = abs(expected), abs(t)
            if abs(expected - t) > max(1.0, 0.01 * abs(t)):
                msg = f"Row {i}: {_fmt(q)} x {_fmt(p)} = {_fmt(expected)}, printed total {_fmt(t)}."
                out.append(_issue(self.code, "warning", [f"{self.items_field}[{i}].total"], msg, expected=expected, printed=t))
        return out


class PayableChain:
    """grand_total - insurance share should equal what the patient pays.

    A warning, not an error: deposits ('Tạm ứng') legitimately break the chain on VN invoices.
    """

    code = "PAYABLE_CHAIN_MISMATCH"

    def check(self, fields: Fields, ctx: RuleContext) -> list[ValidationIssue]:
        grand = _num(_value(fields, "grand_total"))
        insured = _num(_value(fields, "insurance_covered_amount"))
        payable = _num(_value(fields, "patient_payable"))
        if grand is None or insured is None or payable is None:
            return []
        expected = grand - insured
        if abs(expected - payable) <= max(1.0, 0.005 * abs(grand)):
            return []
        msg = f"Grand total {_fmt(grand)} - insurance {_fmt(insured)} = {_fmt(expected)}, but patient payable is {_fmt(payable)}."
        names = ["grand_total", "insurance_covered_amount", "patient_payable"]
        return [_issue(self.code, "warning", names, msg, expected=expected, printed=payable)]


class AmountInWords:
    """The amount in words must match a printed total (a classic tampering signal on VN invoices)."""

    code = "AMOUNT_IN_WORDS_MISMATCH"
    targets = ("grand_total", "patient_payable")

    def check(self, fields: Fields, ctx: RuleContext) -> list[ValidationIssue]:
        text = _value(fields, "amount_in_words")
        if not isinstance(text, str) or not text.strip():
            return []
        amounts = {n: v for n in self.targets if (v := _num(_value(fields, n))) is not None}
        if not amounts:
            return []
        words = words_to_number(text, ctx.language if ctx.language in {"vi", "en"} else None)
        if words is None:  # bilingual invoices: an 'en' doc may still spell the total in Vietnamese
            words = words_to_number(text)
        if words is None:
            msg = "Could not read the amount in words; it was not checked."
            return [_issue("AMOUNT_IN_WORDS_UNPARSEABLE", "info", ["amount_in_words"], msg, text=text)]
        if any(abs(words - v) <= 1.0 for v in amounts.values()):
            return []
        msg = f"Amount in words reads {words:,} but printed totals are " + ", ".join(f"{n}={_fmt(v)}" for n, v in amounts.items()) + "."
        return [_issue(self.code, "error", ["amount_in_words", *amounts], msg, words_value=words, **amounts)]


# --------------------------------------------------------------------------------------------
# Clinical rules
# --------------------------------------------------------------------------------------------


class LabFlagConsistent:
    """A printed H/L flag that contradicts result vs reference range (misprint or tampering)."""

    code = "LAB_FLAG_INCONSISTENT"

    def __init__(self, tests_field: str = "tests") -> None:
        self.tests_field = tests_field

    def check(self, fields: Fields, ctx: RuleContext) -> list[ValidationIssue]:
        out = []
        for i, row in enumerate(_rows(fields, self.tests_field)):
            flag = row.get("flag")
            if row.get("flag_source") != "printed" or flag not in {"normal", "high", "low"}:
                continue
            result, ref = row.get("result"), row.get("reference_range")
            derived = derive_flag(result if isinstance(result, str) else None, ref if isinstance(ref, str) else None)
            if derived is not None and derived != flag:
                name = row.get("test_name") or f"row {i}"
                msg = f"{name}: printed flag '{flag}' but {result} vs {ref} is '{derived}'."
                out.append(_issue(self.code, "warning", [f"{self.tests_field}[{i}].flag"], msg, printed=flag, derived=derived))
        return out


class Icd10Format:
    """Printed ICD-10 codes must look like 'K35' / 'K35.8'. Details carry a normalized suggestion."""

    code = "ICD10_FORMAT_INVALID"

    def __init__(self, *field_names: str) -> None:
        self.field_names = field_names or ("diagnosis",)

    def check(self, fields: Fields, ctx: RuleContext) -> list[ValidationIssue]:
        out = []
        for name in self.field_names:
            for path, code in self._codes(name, _value(fields, name)):
                # Dagger/asterisk marks ('A15.0†', 'G01*') are valid ICD-10 notation used on VN forms.
                if not ICD10_RE.fullmatch(code.strip().rstrip("†*+").rstrip()):
                    msg = f"ICD-10 code {code!r} is not in the expected format (e.g. K35.8)."
                    out.append(_issue(self.code, "warning", [path], msg, code=code, suggested=normalize_icd10(code)))
        return out

    @staticmethod
    def _codes(name: str, diagnosis: Any) -> list[tuple[str, str]]:
        if not isinstance(diagnosis, dict):
            return []
        found = []
        primary = diagnosis.get("primary")
        if isinstance(primary, dict) and isinstance(primary.get("icd10_code"), str) and primary["icd10_code"].strip():
            found.append((f"{name}.primary.icd10_code", primary["icd10_code"]))
        secondary = diagnosis.get("secondary")
        for i, entry in enumerate(secondary if isinstance(secondary, list) else []):
            if isinstance(entry, dict) and isinstance(entry.get("icd10_code"), str) and entry["icd10_code"].strip():
                found.append((f"{name}.secondary[{i}].icd10_code", entry["icd10_code"]))
        return found


class ProcedureDatesInStay:
    """A procedure dated outside the admission..discharge window. Raw (non-ISO) dates are skipped."""

    code = "PROCEDURE_DATE_OUT_OF_STAY"

    def __init__(self, procedures_field: str = "procedures_performed") -> None:
        self.procedures_field = procedures_field

    def check(self, fields: Fields, ctx: RuleContext) -> list[ValidationIssue]:
        start, end = _iso(_value(fields, "admission_date")), _iso(_value(fields, "discharge_date"))
        if start is None and end is None:
            return []
        out = []
        for i, row in enumerate(_rows(fields, self.procedures_field)):
            d = _iso(row.get("date"))
            if d is None or ((start is None or d >= start) and (end is None or d <= end)):
                continue
            stay = f"{start.isoformat() if start else '?'}..{end.isoformat() if end else '?'}"
            msg = f"Procedure {row.get('name') or i} dated {d.isoformat()} is outside the stay {stay}."
            out.append(_issue(self.code, "warning", [f"{self.procedures_field}[{i}].date"], msg, date=d.isoformat()))
        return out


# --------------------------------------------------------------------------------------------
# Canonical rule sets
# --------------------------------------------------------------------------------------------


def rules_for(doc_type: str) -> list[Rule]:
    """Canonical rules per built-in type (REQUIRED_FIELD_MISSING is added by the pipeline).

    Unknown types get no rules: a plugin type brings its own via ``DocTypeSpec.rules``.
    """
    if doc_type == "receipt":
        return [
            DateValid("date"),
            NotFutureDate("date"),
            PositiveAmounts(),
            TotalsMatch(),
            LineArithmetic(),
            PayableChain(),
            AmountInWords(),
        ]
    if doc_type == "discharge_summary":
        return [
            DateValid("admission_date", "discharge_date", "follow_up_date", "sick_leave_from", "sick_leave_to"),
            NotFutureDate("admission_date", "discharge_date"),
            DateOrder("admission_date", "discharge_date", "DISCHARGE_BEFORE_ADMISSION"),
            Icd10Format(),
            ProcedureDatesInStay(),
        ]
    if doc_type == "lab_report":
        return [
            DateValid("date", "collection_date"),
            NotFutureDate("date", "collection_date"),
            DateOrder("collection_date", "date", "COLLECTION_AFTER_REPORT", severity="warning"),
            LabFlagConsistent(),
        ]
    if doc_type == "prescription":
        return [DateValid("date"), NotFutureDate("date"), Icd10Format()]
    return []
