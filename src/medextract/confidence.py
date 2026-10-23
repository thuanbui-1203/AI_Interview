"""Confidence scoring: model self-report (a discrete bucket) adjusted by external evidence.

LLM self-reported floats cluster around 0.9, so the model picks a rubric bucket instead and code
turns it into a number. Every adjustment is a cap or a multiplier with a reason, recorded on the
field (``FieldResult.trace``) so a reviewer can see *why* a field scored low.
"""

from __future__ import annotations

import re
import unicodedata

from medextract.core.fields import Bucket
from medextract.core.models import Adjustment, ClassificationInfo, FieldResult, ValidationIssue

BUCKET_BASE: dict[str, float] = {
    Bucket.certain: 0.97,
    Bucket.high: 0.88,
    Bucket.medium: 0.65,
    Bucket.low: 0.35,
    Bucket.none: 0.10,
}

# Caps / multipliers, kept in one place so the eval report can cite them.
NULL_CAP = 0.15
UNGROUNDED_CAP = 0.5  # value given without any evidence quote
NOT_IN_TEXT_LAYER_FACTOR = 0.6  # evidence quote not found in the PDF text layer
VALUE_NOT_IN_EVIDENCE_FACTOR = 0.7  # quote does not contain the value it supposedly supports
RAW_MISMATCH_CAP = 0.5  # model's value disagrees with code's parse of the printed raw text
AMBIGUOUS_DATE_CAP = 0.75
STATUS_CAPS = {"partial": 0.7, "ambiguous": 0.6, "illegible": 0.35}
ISSUE_CAPS = {"error": 0.5, "warning": 0.75}
EMPTY_TABLE_CONF = 0.8
# Below this a field should be checked by a human (the UI paints it amber/red; the eval uses it for
# "did we flag the blank/smudged/implicated field").
REVIEW_THRESHOLD = 0.85  # section present but no rows (e.g. no procedures performed)


class Scorer:
    """Accumulates adjustments for one field."""

    def __init__(self, base: float) -> None:
        self.value = base
        self.trace: list[Adjustment] = []

    def cap(self, limit: float, reason: str) -> None:
        if self.value > limit:
            self.trace.append(Adjustment(reason=reason, before=self.value, after=limit))
            self.value = limit

    def mul(self, factor: float, reason: str) -> None:
        after = round(self.value * factor, 4)
        self.trace.append(Adjustment(reason=reason, before=self.value, after=after))
        self.value = after


def base_of(bucket: str | None) -> float:
    return BUCKET_BASE.get(bucket or Bucket.none, BUCKET_BASE[Bucket.none])


# --- grounding --------------------------------------------------------------------------------
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def canon(text: str) -> str:
    """Comparison form: NFC, casefold, punctuation -> space, collapsed whitespace."""
    t = unicodedata.normalize("NFC", text).casefold()
    return " ".join(_PUNCT.sub(" ", t).split())


def token_coverage(needle: str, haystack: str) -> float:
    """Share of the needle's tokens present in the haystack (order-insensitive, cheap, no deps)."""
    n = canon(needle).split()
    if not n:
        return 0.0
    hay = set(canon(haystack).split())
    return sum(1 for tok in n if tok in hay) / len(n)


# Numbers without spaces ("150.000", "1,234.50"), plus space-grouped thousands ("1 250 000"). Two
# separate patterns: allowing spaces inside one pattern glued "1 10 10" (qty, price, total) into 11010.
_NUMBER = re.compile(r"\d[\d.,]*\d|\d")
_SPACED_NUMBER = re.compile(r"(?<![\d.,])\d{1,3}(?: \d{3})+(?:[.,]\d{1,2})?(?![\d])")


def _number_readings(token: str) -> set[float]:
    """Plausible values of a printed number: all separators as thousands, or a 1-2 digit decimal tail.

    '15.750.000' -> {15750000}; '1,234.50' -> {123450, 1234.5}. A 3-digit tail is never a decimal,
    which is exactly what catches '15.750.000' misread as 15.75.
    """
    t = token.replace(" ", "")
    readings = {float(re.sub(r"\D", "", t))}
    if m := re.search(r"[.,](\d{1,2})$", t):
        readings.add(float((re.sub(r"\D", "", t[: m.start()]) or "0") + "." + m.group(1)))
    return readings


def evidence_in_text(evidence: str, page_text: str) -> bool:
    c_ev, c_txt = canon(evidence), canon(page_text)
    return c_ev in c_txt or token_coverage(evidence, page_text) >= 0.8


def value_supported_by(value: object, evidence: str) -> bool:
    """Does the quote plausibly contain the value? Numbers compare by digits, text by tokens."""
    if value is None:
        return True
    if isinstance(value, (int, float)):
        v = abs(float(value))
        tokens = _NUMBER.findall(evidence) + _SPACED_NUMBER.findall(evidence)
        return any(abs(r - v) <= 0.005 for tok in tokens for r in _number_readings(tok))
    text = str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):  # ISO date: day digits must appear in the quote
        d = text.split("-")[2]
        digits_ev = re.sub(r"\D", " ", evidence).split()
        return str(int(d)) in {str(int(x)) for x in digits_ev if x}
    if token_coverage(text, evidence) >= 0.6:
        return True
    # Long free text: the quote (max ~80 chars) is only a slice of the value, so check the other way.
    quote = evidence.split(":", 1)[-1] if ":" in evidence[:40] else evidence
    return len(canon(quote).split()) >= 4 and token_coverage(quote, text) >= 0.8


# --- field-level and document-level scoring ---------------------------------------------------
def apply_issue_caps(fields: dict[str, FieldResult], issues: list[ValidationIssue]) -> None:
    """Fields named in validation issues cannot keep a high confidence."""
    for issue in issues:
        limit = ISSUE_CAPS.get(issue.severity)
        if limit is None or issue.code == "REQUIRED_FIELD_MISSING":
            continue
        for path in issue.fields:
            name = path.split("[")[0].split(".")[0]
            f = fields.get(name)
            if f is None or f.value is None:
                continue
            if f.confidence > limit:
                f.trace.append(Adjustment(reason=f"issue:{issue.code}", before=f.confidence, after=limit))
                f.confidence = limit


def document_confidence(cls: ClassificationInfo, observed_type: str | None) -> tuple[float, list[ValidationIssue]]:
    """Classifier top score, discounted by a small margin; capped on disagreement / non-medical."""
    issues: list[ValidationIssue] = []
    scores = sorted(cls.scores.values(), reverse=True) or [cls.confidence]
    top = scores[0]
    margin = top - (scores[1] if len(scores) > 1 else 0.0)
    conf = top - 0.5 * max(0.0, 0.3 - margin)  # margin >= 0.3 costs nothing; a coin-flip costs 0.15
    if observed_type and observed_type.strip().lower() != cls.document_type:
        conf = min(conf, 0.5)
        issues.append(
            ValidationIssue(
                code="CLASSIFICATION_DISAGREEMENT",
                severity="warning",
                message=f"classifier said {cls.document_type!r} but the extractor observed {observed_type!r}",
                details={"classified": cls.document_type, "observed": observed_type},
            )
        )
    if not cls.is_medical_document:
        conf = min(conf, 0.3)
        issues.append(
            ValidationIssue(
                code="NOT_MEDICAL_DOCUMENT_SUSPECTED",
                severity="warning",
                message="the document does not look like a medical document; extraction forced into the closest type",
                details={"cues": cls.cues},
            )
        )
    conf = round(max(0.0, min(1.0, conf)), 3)
    if conf < 0.6:
        issues.append(
            ValidationIssue(
                code="CLASSIFICATION_LOW_CONFIDENCE",
                severity="warning",
                message=f"document type confidence is low ({conf:.2f}); route to a human",
            )
        )
    return conf, issues
