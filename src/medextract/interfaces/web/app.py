"""Small FastAPI app: one upload page and a JSON API.

POST /api/extract  (multipart: file, optional type, verbose)  -> the public JSON (or verbose)
GET  /api/types                                                  -> registered types and fields
GET  /                                                           -> single-page UI
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from medextract.core.pipeline import Extractor
from medextract.core.registry import default_registry
from medextract.errors import MedExtractError

STATIC = Path(__file__).parent / "index.html"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

app = FastAPI(title="medextract", version="0.1.0", description="Medical document extraction for insurance claims.")


@lru_cache(maxsize=1)
def get_extractor() -> Extractor:
    return Extractor()


@app.exception_handler(MedExtractError)
async def _domain_error(_, exc: MedExtractError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content={"error": exc.code, "message": str(exc)})


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return STATIC.read_text(encoding="utf-8")


@app.get("/api/types")
def types() -> dict:
    return {s.name: {"required": list(s.required_fields), "fields": s.output_fields} for s in default_registry().all()}


@app.post("/api/extract")
async def extract(
    file: Annotated[UploadFile, File()],
    extractor: Annotated[Extractor, Depends(get_extractor)],
    doc_type: Annotated[str | None, Form(alias="type")] = None,
    verbose: Annotated[bool, Form()] = False,
) -> dict:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file larger than 20 MB")
    result = await extractor.extract_bytes(data, source_name=file.filename, expected_type=doc_type or None)
    return result.to_public(verbose=verbose)
