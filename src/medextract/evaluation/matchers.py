"""Field comparison against ground truth, typed by field kind.

Each comparison yields an ``Outcome``: correct / wrong / hallucinated (truth null, prediction
not) / missed (truth present, prediction null). Lists are compared row by row after matching
predicted rows to truth rows, so one misread row doesn't make the whole table "wrong" in the
cell-level metric (the field-level verdict still requires the whole table to be right).
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any, Literal

from medextract.confidence import canon

Verdict = Literal["correct", "wrong", "hallucinated", "missed"]

LONG_TEXT_FIELDS = {"discharge_instructions", "amount_in_words"}
ROW_KEY = {"items": "description", "tests": "test_name", "medications": "name", "procedures_performed": "name"}


@dataclass
class Outcome:
    verdict: Verdict
    cells_total: int = 1
    cells_correct: int = 0
    detail: str = ""

    @property
    def correct(self) -> bool:
        return self.verdict == "correct"


def compare(name: str, pred: Any, truth: Any) -> Outcome:
    if truth is None and pred is None:
        return Outcome("correct", cells_correct=1)
    if truth is None:
        return Outcome("hallucinated", detail=f"pred={_short(pred)}")
    if pred is None:
        return Outcome("missed", detail=f"truth={_short(truth)}")
    if isinstance(truth, list):
        return _compare_rows(name, pred if isinstance(pred, list) else [], truth)
    if isinstance(truth, dict):  # diagnosis
        return _compare_diagnosis(pred if isinstance(pred, dict) else {}, truth)
    ok = (
        text_equal(str(pred), str(truth), long=name in LONG_TEXT_FIELDS)
        if isinstance(truth, str)
        else values_equal(pred, truth)
    )
    return Outcome("correct" if ok else "wrong", cells_correct=int(ok), detail="" if ok else f"pred={_short(pred)} truth={_short(truth)}")


def values_equal(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(b, bool) or isinstance(a, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= max(0.01, 0.001 * abs(b))
    if isinstance(b, str):
        return text_equal(str(a), b)
    return a == b


def text_equal(a: str, b: str, long: bool = False) -> bool:
    ca, cb = canon(a), canon(b)
    if ca == cb:
        return True
    if long:  # free text: token F1
        return token_f1(ca, cb) >= 0.8
    return difflib.SequenceMatcher(None, ca, cb).ratio() >= 0.9


def token_f1(a: str, b: str) -> float:
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return 0.0
    common = sum(min(ta.count(t), tb.count(t)) for t in set(ta))
    if not common:
        return 0.0
    p, r = common / len(ta), common / len(tb)
    return 2 * p * r / (p + r)


def _compare_rows(name: str, pred: list[dict], truth: list[dict]) -> Outcome:
    if not truth:
        ok = not pred
        return Outcome("correct" if ok else "wrong", cells_correct=int(ok), detail="" if ok else f"{len(pred)} extra rows")
    key = ROW_KEY.get(name)
    remaining = list(range(len(pred)))
    total = correct = 0
    problems: list[str] = []
    for t in truth:
        cells = [c for c in t if c != "flag_source"]
        total += len(cells)
        j = _best_row(t, pred, remaining, key)
        if j is None:
            problems.append(f"missing row {_short(t.get(key))}")
            continue
        remaining.remove(j)
        p = pred[j]
        for c in cells:
            if values_equal(p.get(c), t.get(c)):
                correct += 1
            else:
                problems.append(f"{_short(t.get(key))}.{c}: pred={_short(p.get(c))} truth={_short(t.get(c))}")
    if remaining:
        problems.append(f"{len(remaining)} extra rows")
    ok = correct == total and not remaining
    return Outcome("correct" if ok else "wrong", cells_total=total, cells_correct=correct, detail="; ".join(problems[:6]))


def _best_row(t: dict, pred: list[dict], candidates: list[int], key: str | None) -> int | None:
    if not candidates:
        return None
    if key is None:
        return candidates[0]
    target = canon(str(t.get(key) or ""))
    scored = [(difflib.SequenceMatcher(None, canon(str(pred[j].get(key) or "")), target).ratio(), j) for j in candidates]
    best, j = max(scored)
    return j if best >= 0.5 else None


def _compare_diagnosis(pred: dict, truth: dict) -> Outcome:
    def dx_ok(p: dict | None, t: dict | None) -> bool:
        if t is None or p is None:
            return t is None and p is None
        desc_ok = t.get("description") is None or (
            p.get("description") is not None and text_equal(p["description"], t["description"], long=True)
        )
        code_ok = (p.get("icd10_code") or "").upper().replace(" ", "") == (t.get("icd10_code") or "").upper().replace(" ", "")
        return desc_ok and code_ok

    cells = 1 + len(truth.get("secondary") or [])
    correct = int(dx_ok(pred.get("primary"), truth.get("primary")))
    pred_sec = list(pred.get("secondary") or [])
    for t in truth.get("secondary") or []:
        match = next((p for p in pred_sec if dx_ok(p, t)), None)
        if match is not None:
            pred_sec.remove(match)
            correct += 1
    ok = correct == cells and not pred_sec
    return Outcome("correct" if ok else "wrong", cells_total=cells, cells_correct=correct, detail="" if ok else f"pred={_short(pred)}")


def _short(v: Any, n: int = 60) -> str:
    s = repr(v)
    return s if len(s) <= n else s[: n - 3] + "..."
