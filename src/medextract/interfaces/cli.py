"""Command line: ``medextract extract FILE...`` (JSON to stdout or one file per document)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from medextract.errors import MedExtractError

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Medical document extraction for insurance claims.")


def _utf8_stdio() -> None:
    # Windows consoles default to cp1252 and crash on Vietnamese text; force UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


@app.command()
def extract(
    files: Annotated[list[Path], typer.Argument(exists=True, dir_okay=False, readable=True)],
    doc_type: Annotated[str | None, typer.Option("--type", help="Skip classification: receipt|discharge_summary|lab_report|prescription")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Include raw text, evidence, confidence trace, usage and cost")] = False,
    out: Annotated[Path | None, typer.Option(help="Write <name>.json per input into this directory instead of stdout")] = None,
) -> None:
    """Extract structured data from medical documents (PDF / PNG / JPEG / WEBP / TIFF)."""
    _utf8_stdio()
    from medextract.core.pipeline import Extractor

    extractor = Extractor()
    failed = 0
    for path in files:
        try:
            result = asyncio.run(extractor.extract_bytes(path.read_bytes(), source_name=path.name, expected_type=doc_type))
            payload = result.to_public(verbose=verbose)
        except MedExtractError as exc:
            failed += 1
            payload = {"error": exc.code, "message": str(exc), "file": path.name}
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if out:
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{path.stem}.json").write_text(text, encoding="utf-8")
            typer.echo(f"{path.name} -> {out / (path.stem + '.json')}", err=True)
        else:
            typer.echo(text)
    raise typer.Exit(code=2 if failed else 0)


@app.command("eval")
def eval_cmd(
    samples: Annotated[Path, typer.Option(help="Directory with <set>/<doc_id>/truth.json")] = Path("samples"),
    out: Annotated[Path, typer.Option(help="Report directory")] = Path("reports/latest"),
    sets: Annotated[str, typer.Option(help="Comma-separated sets: core,stress")] = "core,stress",
    concurrency: Annotated[int, typer.Option(min=1, max=16)] = 4,
    resume: Annotated[bool, typer.Option(help="Re-score results already saved in --out instead of calling the API again")] = False,
) -> None:
    """Run the extractor over the sample set and write report.md / report.json (spends API money unless replaying)."""
    _utf8_stdio()
    from medextract.evaluation.harness import run_eval

    report = asyncio.run(run_eval(samples, out, [s.strip() for s in sets.split(",") if s.strip()], concurrency=concurrency, resume=resume))
    keys = ["documents", "documents_scored", "classification_accuracy", "field_accuracy", "required_field_accuracy", "hallucination_rate",
            "validation_recall", "cost_usd_total", "cost_usd_per_doc", "latency_ms_p50"]  # fmt: skip
    typer.echo(json.dumps({k: report[k] for k in keys} | {"ece": report["calibration"]["ece"]}, indent=2))
    typer.echo(f"report: {out / 'report.md'}", err=True)


@app.command()
def models(
    provider: Annotated[str, typer.Option(help="gemini | groq")] = "gemini",
) -> None:
    """List the models this provider's API key can call (verify names before setting MEDX_*_MODEL)."""
    _utf8_stdio()
    from medextract.config import Settings

    settings = Settings(provider=provider)
    if provider == "groq":
        import groq

        secret = settings.groq_api_key
        client = groq.Groq(api_key=secret.get_secret_value() if secret else None)
        for m in client.models.list().data:
            typer.echo(f"{m.id}\t{m.owned_by}")
        return
    from medextract.providers.gemini_provider import GeminiProvider

    for m in GeminiProvider(settings).client.models.list():
        if "generateContent" in (getattr(m, "supported_actions", None) or []):
            typer.echo(f"{m.name.removeprefix('models/')}\t{getattr(m, 'display_name', '')}")


@app.command()
def types() -> None:
    """List the registered document types and their output fields."""
    _utf8_stdio()
    from medextract.core.registry import default_registry

    for spec in default_registry().all():
        typer.echo(f"{spec.name}: {', '.join(spec.output_fields)}")


def main() -> None:
    app()
