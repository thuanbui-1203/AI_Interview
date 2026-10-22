"""Locale-aware re-parsing of printed values (dates, money, lab ranges, ICD-10 codes).

The model reports what it *read* (``raw``) and what it *thinks* it means (``value``). These
pure functions re-derive the meaning from ``raw`` in code, so a disagreement (e.g. the model
turning ``15.750.000 đ`` into ``15.75``) can be caught instead of trusted. Every function is
total: bad input yields an ``error``/``None``, never an exception.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Literal

# --------------------------------------------------------------------------------------------
# Shared text helpers
# --------------------------------------------------------------------------------------------

_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
_COMBINING = re.compile(r"[̀-ͯ]")  # Latin diacritics only; Thai marks are kept


def thai_digits_to_ascii(text: str) -> str:
    return text.translate(_THAI_DIGITS)


def fold(text: str) -> str:
    """Lowercase, ASCII digits, Vietnamese diacritics removed (``Tháng`` -> ``thang``).

    Only U+0300..U+036F marks are dropped, so Thai vowel/tone marks survive and Thai month
    names still match.
    """
    text = unicodedata.normalize("NFD", thai_digits_to_ascii(text))
    text = _COMBINING.sub("", text).replace("đ", "d").replace("Đ", "D")
    return unicodedata.normalize("NFC", text).lower()


def _norm_language(language: str | None) -> str | None:
    """Only ``vi``/``th``/``en`` change parsing behaviour; ``mixed``/``other`` mean unknown."""
    lang = (language or "").strip().lower()[:2]
    return lang if lang in {"vi", "th", "en"} else None


# --------------------------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------------------------

Calendar = Literal["gregorian", "buddhist"]


@dataclass(frozen=True)
class DateParse:
    value: date | None = None
    iso: str | None = None
    ambiguous: bool = False  # day/month order could not be decided from the text alone
    calendar: Calendar = "gregorian"
    error: str | None = None  # "invalid_date" (not a real day) | "unparseable"


_EN_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}  # fmt: skip

_TH_MONTHS = {
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5, "มิถุนายน": 6,
    "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12,
    "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
    "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12,
}  # fmt: skip


def _month_alt(names: dict[str, int]) -> str:
    """Regex alternation, longest first so 'sept' wins over 'sep'; abbreviation dots optional."""
    parts = []
    for name in sorted(names, key=len, reverse=True):
        parts.append(re.escape(name).replace(r"\.", r"\.?"))
    return "|".join(parts)


def _th_month(token: str) -> int | None:
    key = token.replace(".", "")
    for name, num in _TH_MONTHS.items():
        if name.replace(".", "") == key:
            return num
    return None


_EN_MON = _month_alt(_EN_MONTHS)
_TH_MON = _month_alt(_TH_MONTHS)
_YEAR = r"(?:(?:b\.?e\.?|พ\.?ศ\.?)\s*)?(\d{4}|\d{2})(?!\d)"

# Times are removed first so '14:30 12/03/2024' or '08 giờ 30 phút, ngày ...' cannot be
# mistaken for a day/month pair.
_TIME_RES = (
    re.compile(r"(?<!\d)\d{1,2}\s*:\s*\d{2}(?:\s*:\s*\d{2})?(?:\s*(?:am|pm|a\.m\.|p\.m\.|sa|ch))?(?!\d)"),
    re.compile(r"(?<!\d)\d{1,2}\s*(?:gio|h)\s*(?:\d{1,2}\s*(?:phut|p|')?)?(?![a-z\d])"),
)

_VI_RE = re.compile(r"(?<!\d)(\d{1,2})\s*thang\s*(\d{1,2})\s*(?:,|/|nam)?\s*" + _YEAR)
_EN_DMY_RE = re.compile(
    r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?[\s\-/.]*(" + _EN_MON + r")(?![a-z])\.?[\s\-/.,]*" + _YEAR
)
_EN_MDY_RE = re.compile(r"(?<![a-z])(" + _EN_MON + r")(?![a-z])\.?\s*(\d{1,2})(?!\d)(?:st|nd|rd|th)?\s*,?\s*" + _YEAR)
_TH_RE = re.compile(r"(?<!\d)(\d{1,2})\s*(" + _TH_MON + r")\s*" + _YEAR)
_ISO_RE = re.compile(r"(?<!\d)(\d{4})\s*([/.\-])\s*(\d{1,2})\s*\2\s*(\d{1,2})(?!\d)")
_NUM_RE = re.compile(r"(?<!\d)(\d{1,2})\s*([/.\-])\s*(\d{1,2})\s*\2\s*(\d{4}|\d{2})(?!\d)")


def _strip_times(text: str) -> str:
    for rx in _TIME_RES:
        text = rx.sub(" ", text)
    return text


def _full_year(year_text: str, language: str | None, today: date) -> tuple[int, Calendar]:
    """Expand 2-digit years and convert Thai Buddhist Era (>= 2400) to Gregorian.

    2-digit years pivot on today: ``<= yy+1`` -> 20xx, else 19xx. For Thai documents a 2-digit
    year above the pivot is read as a short BE year (``67`` -> 2567 BE -> 2024).
    """
    year = int(year_text)
    if len(year_text) == 2:
        pivot = today.year % 100 + 1
        if year <= pivot:
            return 2000 + year, "gregorian"
        if language == "th":
            return 2500 + year - 543, "buddhist"
        return 1900 + year, "gregorian"
    if year >= 2400:
        return year - 543, "buddhist"
    return year, "gregorian"


def _build(year: int, month: int, day: int, calendar: Calendar, ambiguous: bool) -> DateParse:
    try:
        value = date(year, month, day)
    except ValueError:
        return DateParse(ambiguous=ambiguous, calendar=calendar, error="invalid_date")
    return DateParse(value=value, iso=value.isoformat(), ambiguous=ambiguous, calendar=calendar)


def _numeric_order(a: int, b: int, language: str | None) -> tuple[int, int, bool]:
    """Return (day, month, ambiguous) for a numeric 'a/b/yyyy' date.

    VN and TH documents are always day-first. Otherwise day-first is still the default (Asian
    claims), but an order that only works month-first (``03/25``) is read that way, and a pair
    where both readings are valid is reported as ambiguous.
    """
    if language in {"vi", "th"}:
        return a, b, False
    if a <= 12 < b:
        return b, a, False
    return a, b, (a <= 12 and b <= 12 and a != b)


def parse_date(raw: str | None, *, language: str | None = None, today: date | None = None) -> DateParse:
    """Parse a printed date in any of the VN/EN/TH forms seen on medical documents."""
    if not raw or not raw.strip():
        return DateParse(error="unparseable")
    lang = _norm_language(language)
    today = today or date.today()
    text = _strip_times(fold(raw))

    if m := _VI_RE.search(text):
        year, cal = _full_year(m.group(3), lang, today)
        return _build(year, int(m.group(2)), int(m.group(1)), cal, False)
    if m := _TH_RE.search(text):
        month = _th_month(m.group(2))
        if month is not None:
            year, cal = _full_year(m.group(3), "th", today)
            return _build(year, month, int(m.group(1)), cal, False)
    if m := _EN_DMY_RE.search(text):
        year, cal = _full_year(m.group(3), lang, today)
        return _build(year, _EN_MONTHS[m.group(2).rstrip(".")], int(m.group(1)), cal, False)
    if m := _EN_MDY_RE.search(text):
        year, cal = _full_year(m.group(3), lang, today)
        return _build(year, _EN_MONTHS[m.group(1).rstrip(".")], int(m.group(2)), cal, False)
    if m := _ISO_RE.search(text):
        year, cal = _full_year(m.group(1), lang, today)
        return _build(year, int(m.group(3)), int(m.group(4)), cal, False)
    if m := _NUM_RE.search(text):
        year, cal = _full_year(m.group(4), lang, today)
        # A Buddhist Era year is itself a Thai cue, and Thai dates are day-first.
        day, month, ambiguous = _numeric_order(int(m.group(1)), int(m.group(3)), "th" if cal == "buddhist" else lang)
        return _build(year, month, day, cal, ambiguous)
    return DateParse(error="unparseable")


# --------------------------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------------------------

ZERO_DECIMAL_CURRENCIES = frozenset({"VND", "IDR", "KRW", "JPY"})


@dataclass(frozen=True)
class MoneyParse:
    value: float | None = None
    error: str | None = None  # "unparseable"
    currency: str | None = None  # ISO code detected from the text (or the one passed in)


# Order matters: more specific markers first ('vnđ' before 'đ', 'us$' before 's$' before '$').
_CURRENCY_MARKERS: tuple[tuple[str, str], ...] = (
    ("vnđ", "VND"), ("vnd", "VND"), ("₫", "VND"), ("đồng", "VND"), ("đ", "VND"),
    ("thb", "THB"), ("฿", "THB"), ("บาท", "THB"), ("baht", "THB"),
    ("sgd", "SGD"), ("usd", "USD"), ("us$", "USD"), ("s$", "SGD"), ("$", "USD"),
    ("eur", "EUR"), ("€", "EUR"), ("idr", "IDR"), ("rp", "IDR"), ("krw", "KRW"), ("₩", "KRW"),
    ("jpy", "JPY"), ("¥", "JPY"), ("円", "JPY"), ("myr", "MYR"), ("rm", "MYR"), ("php", "PHP"), ("₱", "PHP"),
)  # fmt: skip

_SPACES = "    '’"
_NUM_RUN = re.compile(r"\d(?:[\d.,    '’]*\d)?")
_NEG_PREFIX = re.compile(r"[-−–—]\s*$")


def detect_currency(text: str) -> str | None:
    low = text.lower()
    for marker, code in _CURRENCY_MARKERS:
        if marker.isalpha() and marker.isascii():
            if re.search(rf"(?<![a-z]){re.escape(marker)}(?![a-z])", low):
                return code
        elif marker in low:
            return code
    return None


def _take_number(text: str) -> tuple[str, int] | None:
    """First numeric run and its start index; space-grouping kept only if groups are 3 digits."""
    m = _NUM_RUN.search(text)
    if not m:
        return None
    run = m.group(0)
    chunks = re.split(f"[{_SPACES}]+", run)
    if len(chunks) > 1 and not all(re.fullmatch(r"\d{3}(?:[.,]\d+)?", c) for c in chunks[1:]):
        run = chunks[0]  # '12 03 2024' or '2 items' - not a space-grouped number
    return "".join(c for c in run if c not in _SPACES), m.start()


def _resolve_separators(num: str, zero_decimal: bool) -> float | None:
    """Turn '1.250.000' / '1,234.56' / '1.250,50' into a float.

    - Both '.' and ',' present: the last one is the decimal point (must occur once).
    - One kind, repeated: always thousands ('1.250.000', '1,25,000').
    - One kind, once: 3 digits after it -> thousands ('1.250' -> 1250, also for unknown
      currency, which matches VN usage); otherwise decimal ('15.75', '3,5') - unless the
      currency has no minor unit (VND/IDR/KRW/JPY), where every separator is thousands.
    """
    seps = [c for c in num if c in ".,"]
    if not seps:
        return float(num)
    last = seps[-1]
    if len(set(seps)) == 2:
        if seps.count(last) != 1:
            return None
        other = "," if last == "." else "."
        return float(num.replace(other, "").replace(last, "."))
    if len(seps) > 1 or zero_decimal:
        return float(num.replace(last, ""))
    head, tail = num.split(last)
    if len(tail) == 3 and len(head) <= 3 and not head.startswith("0"):
        return float(head + tail)
    return float(f"{head}.{tail}")


def parse_money(raw: str | None, *, currency: str | None = None) -> MoneyParse:
    """Parse a printed amount. Negative: leading minus (also '- 1.000') or '(500,000)'.

    ``currency`` (ISO code) overrides the one detected from the text; only zero-decimal vs
    other matters for separator handling. Amounts in words are handled by ``numwords``.
    """
    if not raw or not raw.strip():
        return MoneyParse(error="unparseable", currency=currency)
    text = thai_digits_to_ascii(raw.strip())
    code = (detect_currency(currency) or currency.upper()) if currency else detect_currency(text)
    found = _take_number(text)
    if found is None:
        return MoneyParse(error="unparseable", currency=code)
    num, start = found
    value = _resolve_separators(num, code in ZERO_DECIMAL_CURRENCIES)
    if value is None:
        return MoneyParse(error="unparseable", currency=code)
    before = text[:start]
    negative = bool(_NEG_PREFIX.search(re.sub(r"[^\d\-−–—\s(]", "", before))) or (
        "(" in before and ")" in text[start:]
    )
    return MoneyParse(value=-value if negative else value, currency=code)


# --------------------------------------------------------------------------------------------
# Lab reference ranges and results
# --------------------------------------------------------------------------------------------

RangeKind = Literal["interval", "upper", "lower", "qualitative", "sex_specific", "unparseable"]


@dataclass(frozen=True)
class RangeParse:
    low: float | None = None
    high: float | None = None
    kind: RangeKind = "unparseable"
    qualitative: str | None = None


_LAB_NUM = r"\d+(?:[.,]\d+)?"
_SEX_RE = re.compile(r"(?<![a-z])(?:(?:m|f)\s*[:=]|(?:male|female|nam|nu|men|women)(?![a-z]))")
# Titres/ratios ('1/80', '< 1/40', '1:160') are not plain numbers; reading them as 1.0 gives wrong flags.
_RATIO_RE = re.compile(r"\d\s*[/:]\s*\d")
_INTERVAL_RE = re.compile(rf"(-?{_LAB_NUM})\s*(?:-|–|—|~|to|den)\s*(-?{_LAB_NUM})")
_UPPER_RE = re.compile(rf"(?:<=|=<|≤|<|up to|under|below|duoi|nho hon)\s*({_LAB_NUM})")
_LOWER_RE = re.compile(rf"(?:>=|=>|≥|>|over|above|tren|lon hon)\s*({_LAB_NUM})")


def _lab_number(text: str) -> float:
    """Lab numbers use ',' as decimal in VN ('3,5'), but '150,000' is a thousands group."""
    if "," in text and "." not in text:
        head, tail = text.split(",", 1)
        if len(tail) == 3 and head.lstrip("-") not in {"", "0"}:
            return float(head + tail)
        return float(f"{head}.{tail}")
    return float(text.replace(",", ""))


def parse_reference_range(text: str | None) -> RangeParse:
    """Classify a printed reference range. Bounds are treated as inclusive by ``derive_flag``."""
    if not text or not text.strip():
        return RangeParse()
    folded = fold(text).strip()
    if _SEX_RE.search(folded):
        return RangeParse(kind="sex_specific")
    body = folded.strip("()[]{} ")
    if _RATIO_RE.search(body):
        return RangeParse()
    if m := _UPPER_RE.search(body):
        return RangeParse(high=_lab_number(m.group(1)), kind="upper")
    if m := _LOWER_RE.search(body):
        return RangeParse(low=_lab_number(m.group(1)), kind="lower")
    if m := _INTERVAL_RE.search(body):
        low, high = _lab_number(m.group(1)), _lab_number(m.group(2))
        if low <= high:
            return RangeParse(low=low, high=high, kind="interval")
        return RangeParse()
    if not re.search(r"\d", body):
        return RangeParse(kind="qualitative", qualitative=text.strip())
    return RangeParse()


_RESULT_RE = re.compile(rf"\s*([+-]?{_LAB_NUM})\s*(?:[a-zµμ/%↑↓*(][a-z0-9µμ/%↑↓*^.()\s]*)?")


def parse_numeric_result(text: str | None) -> float | None:
    """A plain numeric result, else None.

    Censored ('<0.5'), semi-quantitative ('1+', '++') and qualitative ('Negative') results
    cannot be compared against a range, so they yield None rather than a misleading number.
    """
    if not text or _RATIO_RE.search(text):
        return None
    m = _RESULT_RE.fullmatch(fold(text))
    return _lab_number(m.group(1)) if m else None


Flag = Literal["normal", "high", "low"]


def derive_flag(result: str | None, reference_range: str | None) -> Flag | None:
    """Flag a numeric result against a numeric range; None whenever that is not decidable."""
    value = parse_numeric_result(result)
    rng = parse_reference_range(reference_range)
    if value is None or rng.kind not in {"interval", "upper", "lower"}:
        return None
    if rng.low is not None and value < rng.low:
        return "low"
    if rng.high is not None and value > rng.high:
        return "high"
    return "normal"


# --------------------------------------------------------------------------------------------
# ICD-10
# --------------------------------------------------------------------------------------------

ICD10_RE = re.compile(r"^[A-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$")
_ICD10_LOOSE = re.compile(r"([A-Z])([0-9]{2})\.?([0-9A-Z]{1,4})?")


def normalize_icd10(code: str | None) -> str | None:
    """Canonical form ('k359' -> 'K35.9'); dagger/asterisk marks ('A15.0†') are dropped."""
    if not code:
        return None
    compact = re.sub(r"[\s†*+]", "", code).upper()
    m = _ICD10_LOOSE.fullmatch(compact)
    if not m:
        return None
    letter, digits, suffix = m.groups()
    return f"{letter}{digits}.{suffix}" if suffix else f"{letter}{digits}"
