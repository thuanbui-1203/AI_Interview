"""The full sample catalog and the manifest built from it."""

from __future__ import annotations

from typing import Any

from samplegen.model import SampleDoc
from samplegen.specs_core import core_docs
from samplegen.specs_stress import stress_docs


def all_docs() -> list[SampleDoc]:
    docs = core_docs() + stress_docs()
    ids = [d.doc_id for d in docs]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate doc_id in catalog")
    return docs


def select(docs: list[SampleDoc], only: str | None) -> list[SampleDoc]:
    """``--only`` matches an exact doc_id or a prefix such as 'core_r01' or 'stress'."""
    if not only:
        return docs
    picked = [d for d in docs if d.doc_id == only or d.doc_id.startswith(only)]
    if not picked:
        raise SystemExit(f"no sample matches --only {only!r}")
    return picked


def rel_file(doc: SampleDoc) -> str:
    return f"{doc.set}/{doc.doc_id}/{doc.file_name}"


def manifest(docs: list[SampleDoc]) -> dict[str, Any]:
    return {
        "version": 1,
        "documents": [
            {
                "id": d.doc_id,
                "set": d.set,
                "type": d.doc_type,
                "language": d.language,
                "variant": d.variant,
                "file": rel_file(d),
                "truth": f"{d.set}/{d.doc_id}/truth.json",
                "pages": d.pages,
                "expected_issues": list(d.expected_issues),
            }
            for d in docs
        ],
    }
