"""Evaluation harness: run the extractor over the sample set and score it against truth.json.

This is a measurement tool, not a test: it spends API money (unless cassettes replay) and its
output is a report - classification accuracy, field accuracy, hallucination rate, calibration,
validation recall, cost per document and latency.
"""

from __future__ import annotations

import asyncio
import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from medextract.confidence import REVIEW_THRESHOLD
from medextract.core.pipeline import Extractor
from medextract.core.registry import default_registry
from medextract.errors import MedExtractError, ProviderUnavailable
from medextract.evaluation.matchers import Outcome, compare

BINS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 0.9), (0.9, 1.0001)]


@dataclass
class FieldEval:
    name: str
    required: bool
    outcome: Outcome
    confidence: float
    truth_null: bool
    pred_null: bool


@dataclass
class DocEval:
    doc_id: str
    set: str
    truth_type: str | None
    pred_type: str | None = None
    doc_confidence: float | None = None
    fields: list[FieldEval] = field(default_factory=list)
    expected_issues: list[str] = field(default_factory=list)
    raised: list[str] = field(default_factory=list)
    low_conf_expected: list[str] = field(default_factory=list)
    low_conf_ok: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    latency_ms: int = 0
    error: str | None = None
    skipped: bool = False
    resumed: bool = False

    @property
    def classified_ok(self) -> bool:
        if self.truth_type is None:  # out-of-scope doc: success = flagged and not trusted
            return "NOT_MEDICAL_DOCUMENT_SUSPECTED" in self.raised and (self.doc_confidence or 1) < 0.5
        return self.pred_type == self.truth_type

    @property
    def missing_issues(self) -> list[str]:
        return [c for c in self.expected_issues if c not in self.raised]


def load_truths(samples_dir: Path, sets: list[str]) -> list[tuple[Path, dict]]:
    out = []
    for truth_path in sorted(samples_dir.glob("*/*/truth.json")):
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        if truth.get("set") in sets:
            out.append((truth_path.parent / truth["file"], truth))
    return out


async def run_eval(
    samples_dir: Path,
    out_dir: Path,
    sets: list[str],
    extractor: Extractor | None = None,
    concurrency: int = 4,
    resume: bool = False,
) -> dict[str, Any]:
    """Score every document; with ``resume`` reuse results already saved in ``out_dir``.

    Free-tier quotas are tiny (20 requests/day/model), so a run must never waste requests: a
    daily-quota error stops scheduling new extractions, and ``resume`` re-scores saved results
    without calling the API, so a run can be finished across quota resets.
    """
    extractor = extractor or Extractor()
    required = {s.name: set(s.required_fields) for s in default_registry().all()}
    items = load_truths(samples_dir, sets)
    (out_dir / "results").mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    quota_gone = asyncio.Event()

    async def one(path: Path, truth: dict) -> DocEval:
        async with sem:
            return await _eval_doc(extractor, path, truth, out_dir, required, quota_gone, resume)

    docs = await asyncio.gather(*(one(p, t) for p, t in items))
    report = summarize(docs)
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    return report


async def _eval_doc(
    extractor: Extractor, path: Path, truth: dict, out_dir: Path, required: dict, quota_gone: asyncio.Event, resume: bool
) -> DocEval:
    d = DocEval(
        doc_id=truth["doc_id"],
        set=truth["set"],
        truth_type=truth.get("document_type"),
        expected_issues=list(truth.get("expected_issues") or []),
        low_conf_expected=list(truth.get("low_confidence_fields") or []),
    )
    saved = out_dir / "results" / f"{d.doc_id}.json"
    if resume and saved.exists():
        out = json.loads(saved.read_text(encoding="utf-8"))
        d.resumed = True
    elif quota_gone.is_set():
        d.error, d.skipped = "skipped: provider daily quota exhausted earlier in this run", True
        return d
    else:
        try:
            result = await extractor.extract_bytes(path.read_bytes(), source_name=path.name)
        except MedExtractError as exc:
            d.error = f"{exc.code}: {exc}"
            if isinstance(exc, ProviderUnavailable) and "daily quota" in str(exc):
                quota_gone.set()
            return d
        out = result.to_public(verbose=True)
        saved.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    score_output(d, out, truth, required)
    return d


def score_output(d: DocEval, out: dict[str, Any], truth: dict, required: dict) -> None:
    """Score one verbose public output (fresh or loaded from disk) against its truth."""
    fields = out.get("fields", {})
    d.pred_type, d.doc_confidence = out.get("document_type"), out.get("confidence")
    d.raised = sorted({i["code"] for i in out.get("validation_errors", [])})
    meta = out.get("meta") or {}
    d.cost_usd = meta.get("total_cost_usd", 0.0)
    d.latency_ms = sum(c.get("latency_ms", 0) for c in meta.get("calls", []))
    if not (d.truth_type and d.pred_type == d.truth_type):
        return
    for name, tval in (truth.get("fields") or {}).items():
        f = fields.get(name) or {}
        pval, conf = f.get("value"), f.get("confidence", 0.0)
        d.fields.append(
            FieldEval(name, name in required.get(d.truth_type, ()), compare(name, pval, tval), conf, tval is None, pval is None)
        )
    d.low_conf_ok = [p for p in d.low_conf_expected if flagged_for_review(fields, p)]


def flagged_for_review(fields: dict[str, Any], path: str) -> bool:
    """Did the output warn a human about this field (null, or confidence below the review threshold)?

    ``path`` may point into a table (``items[3].total``): then a null cell also counts as flagged.
    """
    name = re.split(r"[\[.]", path, maxsplit=1)[0]
    f = fields.get(name)
    if not f or f.get("value") is None or f.get("confidence", 0.0) < REVIEW_THRESHOLD:
        return True
    m = re.fullmatch(r"\w+\[(\d+)\]\.(\w+)", path)
    if m and isinstance(f["value"], list):
        i, cell = int(m.group(1)), m.group(2)
        return i >= len(f["value"]) or f["value"][i].get(cell) is None
    return False


# --- metrics ----------------------------------------------------------------------------------
def summarize(all_docs: list[DocEval]) -> dict[str, Any]:
    # Metrics cover documents that produced an output; failures are reported, never scored as "wrong".
    docs = [d for d in all_docs if not d.error]
    fe = [f for d in docs for f in d.fields]
    # Calibration is measured on extracted values only. A null comes with low confidence by design
    # (the brief: "null with low confidence"), so including nulls would punish correct abstentions.
    pairs = [(f.confidence, f.outcome.correct) for f in fe if not f.pred_null]
    nulls = [f for f in fe if f.pred_null]
    report: dict[str, Any] = {
        "documents": len(all_docs),
        "documents_scored": len(docs),
        "documents_resumed": sum(d.resumed for d in docs),
        "errors": [{"doc_id": d.doc_id, "error": d.error} for d in all_docs if d.error and not d.skipped],
        "skipped": [d.doc_id for d in all_docs if d.skipped],
        "classification_accuracy": _ratio(sum(d.classified_ok for d in docs), len(docs)),
        "field_accuracy": _ratio(sum(f.outcome.correct for f in fe), len(fe)),
        "required_field_accuracy": _ratio(sum(f.outcome.correct for f in fe if f.required), sum(f.required for f in fe)),
        "cell_accuracy": _ratio(sum(f.outcome.cells_correct for f in fe), sum(f.outcome.cells_total for f in fe)),
        "hallucination_rate": _ratio(sum(f.outcome.verdict == "hallucinated" for f in fe), sum(f.truth_null for f in fe)),
        "miss_rate": _ratio(sum(f.outcome.verdict == "missed" for f in fe), len(fe)),
        "calibration": calibration(pairs),
        "null_predictions": len(nulls),
        "null_precision": _ratio(sum(f.truth_null for f in nulls), len(nulls)),  # nulls that were truly absent
        "validation_recall": _ratio(
            sum(len(d.expected_issues) - len(d.missing_issues) for d in docs), sum(len(d.expected_issues) for d in docs)
        ),
        # The point of confidence: of the values we got WRONG, how many did we flag for a human?
        "error_flag_recall": _ratio(
            sum(f.confidence < REVIEW_THRESHOLD for f in fe if not f.pred_null and not f.outcome.correct),
            sum(1 for f in fe if not f.pred_null and not f.outcome.correct),
        ),
        "low_confidence_recall": _ratio(sum(len(d.low_conf_ok) for d in docs), sum(len(d.low_conf_expected) for d in docs)),
        "cost_usd_total": round(sum(d.cost_usd for d in docs), 4),
        "cost_usd_per_doc": round(statistics.mean([d.cost_usd for d in docs]) if docs else 0, 5),
        "latency_ms_p50": _pct([d.latency_ms for d in docs if not d.error], 50),
        "latency_ms_p95": _pct([d.latency_ms for d in docs if not d.error], 95),
        "per_doc": [_doc_row(d) for d in all_docs],
        "per_field": _per_field(fe),
    }
    return report


def calibration(pairs: list[tuple[float, bool]]) -> dict[str, Any]:
    if not pairs:
        return {"ece": None, "brier": None, "bins": []}
    bins, ece = [], 0.0
    for lo, hi in BINS:
        inb = [(c, ok) for c, ok in pairs if lo <= c < hi]
        if not inb:
            bins.append({"range": f"{lo:.1f}-{min(hi, 1):.1f}", "n": 0, "accuracy": None, "mean_conf": None})
            continue
        acc = sum(ok for _, ok in inb) / len(inb)
        mc = sum(c for c, _ in inb) / len(inb)
        ece += len(inb) / len(pairs) * abs(acc - mc)
        bins.append({"range": f"{lo:.1f}-{min(hi, 1):.1f}", "n": len(inb), "accuracy": round(acc, 3), "mean_conf": round(mc, 3)})
    brier = sum((c - (1.0 if ok else 0.0)) ** 2 for c, ok in pairs) / len(pairs)
    hi_conf = [ok for c, ok in pairs if c >= 0.9]
    return {
        "ece": round(ece, 4),
        "brier": round(brier, 4),
        "bins": bins,
        "accuracy_at_0.9": _ratio(sum(hi_conf), len(hi_conf)),
        "coverage_at_0.9": _ratio(len(hi_conf), len(pairs)),
    }


def _per_field(fe: list[FieldEval]) -> dict[str, Any]:
    names = sorted({f.name for f in fe})
    out = {}
    for n in names:
        fs = [f for f in fe if f.name == n]
        out[n] = {
            "n": len(fs),
            "accuracy": _ratio(sum(f.outcome.correct for f in fs), len(fs)),
            "hallucinated": sum(f.outcome.verdict == "hallucinated" for f in fs),
            "missed": sum(f.outcome.verdict == "missed" for f in fs),
            "mean_conf": round(statistics.mean(f.confidence for f in fs), 3),
        }
    return out


def _doc_row(d: DocEval) -> dict[str, Any]:
    wrong = [f"{f.name}: {f.outcome.verdict} {f.outcome.detail}".strip() for f in d.fields if not f.outcome.correct]
    return {
        "doc_id": d.doc_id,
        "set": d.set,
        "truth_type": d.truth_type,
        "pred_type": d.pred_type,
        "doc_confidence": d.doc_confidence,
        "classified_ok": d.classified_ok,
        "fields_correct": f"{sum(f.outcome.correct for f in d.fields)}/{len(d.fields)}",
        "wrong_fields": wrong,
        "missing_expected_issues": d.missing_issues,
        "raised_issues": d.raised,
        "low_conf_ok": f"{len(d.low_conf_ok)}/{len(d.low_conf_expected)}",
        "cost_usd": round(d.cost_usd, 5),
        "latency_ms": d.latency_ms,
        "error": d.error,
    }


def _ratio(a: float, b: float) -> float | None:
    return round(a / b, 4) if b else None


def _pct(xs: list[int], p: int) -> int | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, round(p / 100 * (len(xs) - 1))))
    return xs[k]


def render_markdown(r: dict[str, Any]) -> str:
    def pct(v: float | None) -> str:
        return "n/a" if v is None else f"{v * 100:.1f}%"

    cal = r["calibration"]
    lines = [
        "# Evaluation report",
        "",
        f"Documents scored: **{r['documents_scored']} of {r['documents']}** "
        f"(errors: {len(r['errors'])}, skipped after quota exhaustion: {len(r['skipped'])}, re-scored from saved results: {r['documents_resumed']}). "
        "All metrics below cover scored documents only.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Classification accuracy | {pct(r['classification_accuracy'])} |",
        f"| Field accuracy (all fields) | {pct(r['field_accuracy'])} |",
        f"| Field accuracy (brief-required fields) | {pct(r['required_field_accuracy'])} |",
        f"| Cell accuracy (table cells counted individually) | {pct(r['cell_accuracy'])} |",
        f"| Hallucination rate (value where truth is null) | {pct(r['hallucination_rate'])} |",
        f"| Miss rate (null where truth has a value) | {pct(r['miss_rate'])} |",
        f"| Validation recall (seeded defects caught) | {pct(r['validation_recall'])} |",
        f"| Error flag recall (wrong values with confidence < {REVIEW_THRESHOLD}) | {pct(r['error_flag_recall'])} |",
        f"| Degraded-field flag rate (blank/smudged/implicated fields flagged; informational) | {pct(r['low_confidence_recall'])} |",
        f"| ECE / Brier (on extracted values) | {cal.get('ece')} / {cal.get('brier')} |",
        f"| Null precision (null answers that were truly absent) | {pct(r['null_precision'])} of {r['null_predictions']} |",
        f"| Accuracy at confidence >= 0.9 (coverage) | {pct(cal.get('accuracy_at_0.9'))} ({pct(cal.get('coverage_at_0.9'))}) |",
        f"| Cost per document | ${r['cost_usd_per_doc']:.4f} (total ${r['cost_usd_total']:.4f}) |",
        f"| Latency p50 / p95 | {r['latency_ms_p50']} ms / {r['latency_ms_p95']} ms |",
        "",
        "## Calibration (reliability table)",
        "",
        "| Confidence | n | Accuracy | Mean confidence |",
        "|---|---|---|---|",
    ]
    for b in cal.get("bins", []):
        lines.append(f"| {b['range']} | {b['n']} | {pct(b['accuracy'])} | {b['mean_conf'] if b['mean_conf'] is not None else '-'} |")
    lines += ["", "## Per document", "", "| Doc | Type (truth -> pred) | Conf | Fields | Missing expected issues | Wrong fields |", "|---|---|---|---|---|---|"]
    for d in r["per_doc"]:
        wrong = "<br>".join(w.replace("|", "\\|") for w in d["wrong_fields"][:5]) or "-"
        lines.append(
            f"| {d['doc_id']} | {d['truth_type']} -> {d['pred_type']}{'' if d['classified_ok'] else ' ❌'} | {d['doc_confidence']} | "
            f"{d['fields_correct']} | {', '.join(d['missing_expected_issues']) or '-'} | {wrong} |"
        )
    lines += ["", "## Per field", "", "| Field | n | Accuracy | Hallucinated | Missed | Mean conf |", "|---|---|---|---|---|---|"]
    for n, s in r["per_field"].items():
        lines.append(f"| {n} | {s['n']} | {pct(s['accuracy'])} | {s['hallucinated']} | {s['missed']} | {s['mean_conf']} |")
    if r["errors"]:
        lines += ["", "## Errors", ""] + [f"- {e['doc_id']}: {e['error']}" for e in r["errors"]]
    return "\n".join(lines) + "\n"
