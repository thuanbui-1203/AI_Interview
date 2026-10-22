"""Amounts written in words (Vietnamese 'Bằng chữ', English 'Baht ... only') -> integers.

VN invoices print the total in words as an anti-tampering measure; a digit edited after printing
no longer matches the words. We only need the integer major-unit amount, so cents/satang parts
('... and fifty satang', '... 50/100') are ignored. Anything not confidently understood returns
None - the caller reports "unparseable", never a guessed number.
"""

from __future__ import annotations

import re

from medextract.validation.parsing import fold

# --------------------------------------------------------------------------------------------
# Vietnamese
# --------------------------------------------------------------------------------------------

# Keys are diacritic-folded, so 'năm'/'nam', 'mốt'/'mot', 'tư'/'tu' all resolve the same way.
_VI_DIGITS = {
    "khong": 0, "mot": 1, "hai": 2, "ba": 3, "bon": 4, "tu": 4, "nam": 5, "lam": 5, "nham": 5,
    "sau": 6, "bay": 7, "tam": 8, "chin": 9,
}  # fmt: skip
_VI_SCALES = {"nghin": 1_000, "ngan": 1_000, "trieu": 1_000_000, "ty": 1_000_000_000, "ti": 1_000_000_000}
_VI_FILLERS = {"dong", "chan", "vnd", "va", "tron", "so", "tien"}  # folded forms
_VI_MARKERS = {"trieu", "nghin", "ngan", "tram", "muoi", "linh", "dong", "chan"}
_VI_LABEL = re.compile(r"^(?:so\s+tien\s+)?(?:viet\s+)?bang\s+chu\s*")


def _vi_tokens(text: str) -> list[str]:
    s = fold(text)
    if ":" in s:
        s = s.rsplit(":", 1)[1]
    s = re.sub(r"viet nam dong|dong viet nam", " ", s)
    s = re.sub(r"[^a-z\s]", " ", s).strip()
    s = _VI_LABEL.sub("", s)
    return [t for t in s.split() if t not in _VI_FILLERS]


def _vi_group(tokens: list[str]) -> int | None:
    """A number below 1000: '[d trăm] [(d mươi | mười | linh/lẻ)] [d]'."""
    hundreds = tens = None
    pending: int | None = None
    for tok in tokens:
        if tok in _VI_DIGITS:
            if pending is not None:
                return None
            pending = _VI_DIGITS[tok]
        elif tok == "tram":
            if hundreds is not None or tens is not None:
                return None
            hundreds, pending = (1 if pending is None else pending), None
        elif tok in {"muoi", "chuc"}:  # 'mươi' after a digit multiplies; bare 'mười' is ten
            if tens is not None:
                return None
            tens, pending = (1 if pending is None else pending), None
        elif tok in {"linh", "le"}:
            if tens is not None or pending is not None:
                return None
            tens = 0
        else:
            return None
    return (hundreds or 0) * 100 + (tens or 0) * 10 + (pending or 0)


def _vi_value(tokens: list[str]) -> int | None:
    """Split on the first occurrence of the largest scale word: left * scale + right."""
    scales = [(_VI_SCALES[t], i) for i, t in enumerate(tokens) if t in _VI_SCALES]
    if not scales:
        return _vi_group(tokens)
    scale = max(s for s, _ in scales)
    idx = next(i for s, i in scales if s == scale)
    rest = tokens[idx + 1 :]
    if len(rest) == 1 and rest[0] in _VI_DIGITS:
        return None  # 'một triệu hai' is colloquial 1.2M, formal 1,000,002 needs 'lẻ/linh'
    left = _vi_value(tokens[:idx]) if tokens[:idx] else 1
    right = _vi_value(rest) if rest else 0
    if left is None or right is None or right >= scale:
        return None
    return left * scale + right


def _parse_vi(text: str) -> int | None:
    tokens = _vi_tokens(text)
    if not tokens or not any(t in _VI_DIGITS or t in _VI_SCALES or t in {"muoi", "tram"} for t in tokens):
        return None
    return _vi_value(tokens)


# --------------------------------------------------------------------------------------------
# English
# --------------------------------------------------------------------------------------------

_EN_ONES = {
    "zero": 0, "a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}  # fmt: skip
_EN_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}  # fmt: skip
_EN_SCALES = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
# Words naming the major unit. Number words after one of them are the minor unit (cents).
_EN_UNITS = {
    "baht", "bahts", "dollar", "dollars", "dong", "vnd", "thb", "usd", "sgd", "euro", "euros",
    "pound", "pounds", "ringgit", "rupiah", "peso", "pesos", "yen", "won",
}  # fmt: skip
_EN_FILLERS = {"and", "only", "thai", "us", "singapore", "vietnamese", "exactly", "amount", "total", "the", "of"}


# Words that prove the text spells a number ("a" alone does not).
_EN_NUMBER_WORDS = (set(_EN_ONES) - {"a"}) | set(_EN_TENS) | set(_EN_SCALES) | {"hundred"}


def _en_tokens(text: str) -> list[str]:
    s = text.lower()
    if ":" in s:
        s = s.rsplit(":", 1)[1]
    s = re.sub(r"[^a-z\s]", " ", s.replace("-", " "))
    tokens = s.split()
    out: list[str] = []
    for tok in tokens:
        if tok in _EN_UNITS:
            if any(t in _EN_ONES or t in _EN_TENS for t in out):
                break  # 'baht' after the amount: whatever follows is satang/cents
            continue  # 'Thai Baht fifteen ...': currency named before the amount
        if tok not in _EN_FILLERS:
            out.append(tok)
    return out


def _en_group(tokens: list[str]) -> int | None:
    """A number below 1000 (or 'fifteen hundred'); rejects sequences like 'five six'."""
    value, last = 0, ""
    for tok in tokens:
        if tok in _EN_ONES and _EN_ONES[tok] < 10:
            if last not in {"", "tens", "hundred"} or (last == "tens" and _EN_ONES[tok] == 0):
                return None
            value, last = value + _EN_ONES[tok], "ones"
        elif tok in _EN_ONES:  # ten .. nineteen
            if last not in {"", "hundred"}:
                return None
            value, last = value + _EN_ONES[tok], "teen"
        elif tok in _EN_TENS:
            if last not in {"", "hundred"}:
                return None
            value, last = value + _EN_TENS[tok], "tens"
        elif tok == "hundred":
            if last not in {"", "ones", "teen"} or value >= 100:
                return None
            value, last = (value or 1) * 100, "hundred"
        else:
            return None
    return value


def _en_value(tokens: list[str]) -> int | None:
    scales = [(_EN_SCALES[t], i) for i, t in enumerate(tokens) if t in _EN_SCALES]
    if not scales:
        return _en_group(tokens)
    scale = max(s for s, _ in scales)
    idx = next(i for s, i in scales if s == scale)
    left = _en_value(tokens[:idx]) if tokens[:idx] else 1
    right = _en_value(tokens[idx + 1 :]) if tokens[idx + 1 :] else 0
    if left is None or right is None or right >= scale:
        return None
    return left * scale + right


def _parse_en(text: str) -> int | None:
    tokens = _en_tokens(text)
    if not tokens or not any(t in _EN_NUMBER_WORDS for t in tokens):
        return None
    return _en_value(tokens)


# --------------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------------


def words_to_number(text: str | None, language: str | None = None) -> int | None:
    """Integer amount spelled out in ``text``, or None if not confidently parseable.

    ``language`` 'vi' / 'en' forces a grammar; anything else tries Vietnamese when the text has
    Vietnamese number vocabulary, then English. Thai script words are not supported (None).
    """
    if not text or not text.strip():
        return None
    lang = (language or "").lower()[:2]
    if lang == "vi":
        return _parse_vi(text)
    if lang == "en":
        return _parse_en(text)
    if set(fold(text).split()) & _VI_MARKERS or re.search(r"[^\x00-\x7f]", text):
        result = _parse_vi(text)
        if result is not None:
            return result
    return _parse_en(text)
