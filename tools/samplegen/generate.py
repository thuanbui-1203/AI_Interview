"""Generate the sample documents, their truth.json, the manifest and a contact sheet.

Usage (from the repo root)::

    uv run python tools/samplegen/generate.py [--only ID_OR_PREFIX] [--out samples]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

# Runnable as a plain script: make the ``samplegen`` package importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf

from samplegen.catalog import all_docs, manifest, rel_file, select
from samplegen.contact_sheet import build_contact_sheet
from samplegen.degrade import degrade_pdf
from samplegen.model import SampleDoc, build_truth
from samplegen.render import PdfRenderer, make_env, render_html


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def page_count(pdf: Path) -> int:
    with pymupdf.open(pdf) as doc:
        return doc.page_count


def generate_one(doc: SampleDoc, renderer: PdfRenderer, out_root: Path, tmp: Path) -> None:
    doc_dir = out_root / doc.set / doc.doc_id
    for stale in ("document.pdf", "document.png"):  # a variant change must not leave the old file behind
        (doc_dir / stale).unlink(missing_ok=True)
    html = render_html(doc, make_env())
    pdf = doc_dir / "document.pdf" if doc.degrade == "none" else tmp / f"{doc.doc_id}.pdf"
    try:
        renderer.to_pdf(html, pdf)
    except RuntimeError as exc:
        raise RuntimeError(f"{doc.doc_id}: {exc}") from exc
    # Guard: a layout that overflows onto an extra page would silently change what truth describes.
    pages = page_count(pdf)
    if pages != doc.pages:
        raise RuntimeError(f"{doc.doc_id}: rendered {pages} page(s), spec says {doc.pages}")
    if doc.degrade != "none":
        degrade_pdf(pdf, doc_dir / "document.png", doc.degrade, doc.doc_id)
    write_json(doc_dir / "truth.json", build_truth(doc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="doc_id or doc_id prefix (e.g. core_r01, stress)")
    parser.add_argument("--out", default="samples", type=Path, help="output directory (default: samples)")
    args = parser.parse_args(argv)

    docs = all_docs()
    todo = select(docs, args.only)
    out_root: Path = args.out
    with tempfile.TemporaryDirectory() as tmp, PdfRenderer() as renderer:
        failures: list[str] = []
        for doc in todo:
            started = time.perf_counter()
            try:
                generate_one(doc, renderer, out_root, Path(tmp))
            except RuntimeError as exc:  # keep going so one bad layout reports alongside the others
                failures.append(str(exc))
                print(f"  FAILED {exc}")
                continue
            print(f"  {doc.doc_id:<42} {doc.file_name:<13} {time.perf_counter() - started:5.1f}s")

    # Manifest and contact sheet always describe the whole catalog, so a partial run keeps them complete.
    write_json(out_root / "manifest.json", manifest(docs))
    build_contact_sheet([(d.doc_id, out_root / rel_file(d)) for d in docs], out_root / "contact_sheet.png")
    print(f"wrote {len(todo) - len(failures)} document(s), manifest and contact sheet to {out_root}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
