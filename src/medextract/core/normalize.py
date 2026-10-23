"""Turn the model's structured output into public FieldResults.

For each field this (1) re-parses printed ``raw`` text with deterministic locale rules and
prefers that over the model's own interpretation when they disagree, (2) checks the evidence
quote against the PDF text layer when one exists, and (3) scores confidence from the model's
bucket plus those signals. Output values follow docs/OUTPUT_CONTRACT.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from pydantic import BaseModel

from medextract import confidence as C
from medextract.core.document import Document
from medextract.core.fields import ChoiceField, DateField, DiagnosisField, ListField, MoneyField, Status, TextField
from medextract.core.models import FieldResult, ValidationIssue
from medextract.core.registry import DocTypeSpec
from medextract.validation.parsing import derive_flag, parse_date, parse_money


@dataclass
class Normalized:
    fields: dict[str, FieldResult]
    issues: list[ValidationIssue] = field(default_factory=list)
    language: str | None = None
    currency: str | None = None
    observed_type: str | None = None


def normalize(extraction: BaseModel, spec: DocTypeSpec, doc: Document | None) -> Normalized:
    language = getattr(extraction, "document_language", None)
    currency_field = getattr(extraction, "currency", None)
    currency = _clean_text(currency_field.value).upper() if currency_field and currency_field.value else None
    ctx = _Ctx(
        language=language,
        currency=currency,
        doc=doc,
        magnitudes=frozenset(spec.magnitude_fields),
        inferred=frozenset(spec.inferred_fields),
    )
    out = Normalized(
        fields={}, language=language, currency=currency, observed_type=getattr(extraction, "observed_document_type", None)
    )
    for name in spec.output_fields:
        out.fields[name] = _normalize_field(name, getattr(extraction, name), ctx, out.issues)
    return out


@dataclass
class _Ctx:
    language: str | None
    currency: str | None
    doc: Document | None
    magnitudes: frozenset[str] = frozenset()
    inferred: frozenset[str] = frozenset()

    def page_text(self, page: int | None) -> str | None:
        if self.doc is None or not self.doc.has_text_layer:
            return None
        if page is not None and 1 <= page <= len(self.doc.pages):
            return self.doc.pages[page - 1].text or ""
        return self.doc.full_text  # unknown page: search the whole document


def _normalize_field(name: str, f: Any, ctx: _Ctx, issues: list[ValidationIssue]) -> FieldResult:
    if isinstance(f, ListField):
        return _list_field(name, f, ctx, issues)
    if isinstance(f, DiagnosisField):
        return _diagnosis_field(f, ctx)
    if isinstance(f, DateField):
        return _date_field(name, f, ctx, issues)
    if isinstance(f, MoneyField):
        return _money_field(name, f, ctx, issues)
    if isinstance(f, ChoiceField):
        # The value is a normalized enum ("cash"); the printed wording ("Tiền mặt") is what the quote must hold.
        return _scalar(f, f.value, f.raw, ctx, support=f.raw)
    if isinstance(f, TextField):
        value = _clean_text(f.value)
        # Inferred fields (an ISO code read off a "₫" sign) are never verbatim in the quote: skip that check.
        return _scalar(f, value, None, ctx, support=None if name in ctx.inferred else value)
    raise TypeError(f"unsupported field type for {name}: {type(f).__name__}")


# --- scalar kinds -----------------------------------------------------------------------------
# Row cells not checked against the row's evidence quote: names/descriptions may be abbreviated in the
# quote ("Amoxicillin/Clav..."), and enums/derived cells are normalizations, never verbatim text.
UNCHECKED_ROW_CELLS = {"description", "name", "test_name", "category", "flag", "flag_source"}

_VALUE = object()  # sentinel: "check the final value itself against the evidence"


def _scalar(f: Any, value: Any, raw: str | None, ctx: _Ctx, scorer: C.Scorer | None = None, support: Any = _VALUE) -> FieldResult:
    """Common scoring for scalar fields once the final value is known.

    ``support`` is what the evidence quote must contain: the value by default, the printed wording
    for normalized values, or None to skip the check.
    """
    if f.status in (Status.not_found, Status.illegible) and value is not None and not f.evidence:
        value = None  # the model said it isn't there; don't keep a dangling value
    s = scorer or C.Scorer(C.base_of(f.confidence))
    _score_common(s, f.status, value, f.evidence, f.page, ctx, value if support is _VALUE else support)
    return FieldResult(
        value=value,
        confidence=round(s.value, 3),
        status=str(f.status),
        raw=raw,
        evidence=f.evidence,
        page=f.page,
        model_confidence=str(f.confidence),
        trace=s.trace,
    )


def _score_common(
    s: C.Scorer, status: str, value: Any, evidence: str | None, page: int | None, ctx: _Ctx, support: Any = _VALUE
) -> None:
    if value is None:
        s.cap(C.NULL_CAP, "null_value")
        return
    if cap := C.STATUS_CAPS.get(str(status)):
        s.cap(cap, f"status:{status}")
    if not evidence:
        s.cap(C.UNGROUNDED_CAP, "no_evidence")
        return
    support = value if support is _VALUE else support
    if support is not None and not C.value_supported_by(support, evidence):
        s.mul(C.VALUE_NOT_IN_EVIDENCE_FACTOR, "value_not_in_evidence")
    text = ctx.page_text(page)
    if text is not None and not C.evidence_in_text(evidence, text):
        s.mul(C.NOT_IN_TEXT_LAYER_FACTOR, "evidence_not_in_text_layer")


def _date_field(name: str, f: DateField, ctx: _Ctx, issues: list[ValidationIssue]) -> FieldResult:
    s = C.Scorer(C.base_of(f.confidence))
    model_iso = f.value if _is_iso(f.value) else None
    if not f.raw:
        return _scalar(f, model_iso, None, ctx, s)
    p = parse_date(f.raw, language=ctx.language)
    if p.error == "invalid_date":
        # Printed date is impossible (e.g. 31/02): value null, raw kept -> DATE_INVALID rule fires.
        return _invalid_date(f, s)
    if p.iso is None:  # code could not parse the printed text: fall back to the model's reading
        s.cap(0.6, "raw_unparseable")
        return _scalar(f, model_iso, f.raw, ctx, s)
    value = p.iso
    if p.ambiguous:
        alt = _swap_day_month(p.value) if p.value else None
        if model_iso and alt and model_iso == alt.isoformat():
            value = model_iso  # the model saw context (other dates, locale) that picks the swap
        s.cap(C.AMBIGUOUS_DATE_CAP, "ambiguous_day_month")
        issues.append(
            ValidationIssue(
                code="DATE_AMBIGUOUS_ORDER",
                severity="warning",
                fields=[name],
                message=f"{name} {f.raw!r} could be read day-first or month-first",
                details={"chosen": value, "raw": f.raw},
            )
        )
    elif p.calendar == "buddhist":
        s.cap(C.AMBIGUOUS_DATE_CAP, "buddhist_era_converted")
    if model_iso and model_iso != value and not p.ambiguous:
        s.cap(C.RAW_MISMATCH_CAP, "model_value_differs_from_raw")
        issues.append(_raw_mismatch(name, f.raw, model_iso, value))
    return _scalar(f, value, f.raw, ctx, s)


def _invalid_date(f: DateField, s: C.Scorer) -> FieldResult:
    s.cap(C.NULL_CAP, "invalid_printed_date")
    return FieldResult(
        value=None,
        confidence=round(s.value, 3),
        status=str(f.status),
        raw=f.raw,
        evidence=f.evidence,
        page=f.page,
        model_confidence=str(f.confidence),
        trace=s.trace,
    )


def _money_field(name: str, f: MoneyField, ctx: _Ctx, issues: list[ValidationIssue]) -> FieldResult:
    s = C.Scorer(C.base_of(f.confidence))
    if not f.raw:
        return _scalar(f, f.value, None, ctx, s)
    p = parse_money(f.raw, currency=ctx.currency)
    model_value = f.value
    if name in ctx.magnitudes:  # sign is presentation for these fields ("Less: insurer (10,000)")
        model_value = abs(model_value) if model_value is not None else None
        p = replace(p, value=abs(p.value) if p.value is not None else None)
    if p.value is None:
        s.cap(0.6, "raw_unparseable")
        return _scalar(f, model_value, f.raw, ctx, s)
    value = p.value
    if model_value is not None and not _close(model_value, value):
        s.cap(C.RAW_MISMATCH_CAP, "model_value_differs_from_raw")
        issues.append(_raw_mismatch(name, f.raw, model_value, value))
    return _scalar(f, value, f.raw, ctx, s)


# --- composite kinds --------------------------------------------------------------------------
def _list_field(name: str, f: ListField, ctx: _Ctx, issues: list[ValidationIssue]) -> FieldResult:
    if not f.rows:
        if f.status == Status.not_found:
            return FieldResult(value=None, confidence=C.NULL_CAP, status=str(f.status))
        return FieldResult(value=[], confidence=C.EMPTY_TABLE_CONF, status=str(f.status))
    rows: list[dict[str, Any]] = []
    row_conf: list[float] = []
    trace = []
    for i, row in enumerate(f.rows):
        cells = row.model_dump(exclude={"evidence", "page", "confidence"})
        s = C.Scorer(C.base_of(row.confidence))
        _post_process_row(name, cells, ctx)
        if not row.evidence:
            s.cap(C.UNGROUNDED_CAP, "no_evidence")
        else:
            for cell, v in cells.items():
                if cell not in UNCHECKED_ROW_CELLS and v is not None and not C.value_supported_by(v, row.evidence):
                    s.mul(C.VALUE_NOT_IN_EVIDENCE_FACTOR, f"{cell}_not_in_evidence")
            text = ctx.page_text(row.page)
            if text is not None and not C.evidence_in_text(row.evidence, text):
                s.mul(C.NOT_IN_TEXT_LAYER_FACTOR, "evidence_not_in_text_layer")
        if s.trace:
            trace += [a.model_copy(update={"reason": f"row{i}:{a.reason}"}) for a in s.trace]
        rows.append(cells)
        row_conf.append(s.value)
    conf = min(row_conf)
    if cap := C.STATUS_CAPS.get(str(f.status)):
        conf = min(conf, cap)
    return FieldResult(
        value=rows,
        confidence=round(conf, 3),
        status=str(f.status),
        page=f.rows[0].page,
        row_evidence=[r.evidence for r in f.rows],
        trace=trace,
        model_confidence=",".join(str(r.confidence) for r in f.rows),
    )


def _post_process_row(list_name: str, cells: dict[str, Any], ctx: _Ctx) -> None:
    """Type-specific row normalization that the contract requires."""
    for k, v in cells.items():
        if isinstance(v, str):
            cells[k] = _clean_text(v)
    if list_name == "tests":
        if cells.get("flag"):
            cells["flag_source"] = "printed"
        else:
            derived = derive_flag(cells.get("result"), cells.get("reference_range"))
            cells["flag"] = derived
            cells["flag_source"] = "derived" if derived else None
    elif list_name == "procedures_performed" and cells.get("date"):
        p = parse_date(cells["date"], language=ctx.language)
        if p.iso:
            cells["date"] = p.iso


def _diagnosis_field(f: DiagnosisField, ctx: _Ctx) -> FieldResult:
    if f.primary is None and not f.secondary:
        value = None
    else:
        value = {
            "primary": _dx(f.primary) if f.primary else None,
            "secondary": [_dx(d) for d in f.secondary],
        }
    s = C.Scorer(C.base_of(f.confidence))
    support = f.primary.description if f.primary and f.primary.description else None
    _score_common(s, f.status, support if value is not None else None, f.evidence, f.page, ctx)
    return FieldResult(
        value=value,
        confidence=round(s.value, 3),
        status=str(f.status),
        evidence=f.evidence,
        page=f.page,
        model_confidence=str(f.confidence),
        trace=s.trace,
    )


def _dx(d: Any) -> dict[str, Any]:
    return {"description": _clean_text(d.description), "icd10_code": _clean_text(d.icd10_code)}


# --- helpers ----------------------------------------------------------------------------------
def _clean_text(v: str | None) -> str | None:
    if v is None:
        return None
    v = " ".join(v.split())
    return v or None


def _is_iso(v: str | None) -> bool:
    if not v:
        return False
    try:
        date.fromisoformat(v)
        return len(v) == 10
    except ValueError:
        return False


def _swap_day_month(d: date) -> date | None:
    try:
        return d.replace(month=d.day, day=d.month)
    except ValueError:
        return None


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.01, 0.005 * max(abs(a), abs(b)))


def _raw_mismatch(name: str, raw: str, model_value: Any, parsed: Any) -> ValidationIssue:
    return ValidationIssue(
        code="VALUE_RAW_MISMATCH",
        severity="warning",
        fields=[name],
        message=f"{name}: model read {model_value!r} but the printed text {raw!r} parses as {parsed!r}; using the parsed value",
        details={"raw": raw, "model_value": model_value, "parsed": parsed},
    )
