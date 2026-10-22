"""Record/replay wrapper so golden tests run offline against real recorded answers.

The cassette key hashes everything that can change the model's answer: stage, model, the full
system prompt (so editing a prompt invalidates its cassettes - a stale replay must never hide a
regression), token budget, effort, thinking mode, the output JSON schema and every content part
(image/PDF bytes by their sha256). Files store a byte-free summary of those inputs so a reviewer
can see what a cassette answers without decoding anything.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from medextract.core.models import CallRecord
from medextract.errors import ConfigError, ProviderError
from medextract.providers.base import (
    ContentPart,
    ImagePart,
    LLMProvider,
    StructuredRequest,
    StructuredResponse,
    TextPart,
)

log = logging.getLogger(__name__)

RecordMode = Literal["off", "record", "replay"]


class CassetteMiss(ProviderError):
    code = "cassette_miss"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _part_summary(part: ContentPart) -> dict[str, str]:
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    if isinstance(part, ImagePart):
        return {"type": "image", "media_type": part.media_type, "sha256": _sha256(part.data)}
    return {"type": "pdf", "sha256": _sha256(part.data)}


def key_inputs(req: StructuredRequest[Any]) -> dict[str, Any]:
    """Everything the key is derived from, with bytes replaced by their hashes."""
    schema = json.dumps(req.output_model.model_json_schema(), sort_keys=True, ensure_ascii=False)
    return {
        "stage": req.stage,
        "model": req.model,
        "system": req.system,
        "max_tokens": req.max_tokens,
        "effort": req.effort,
        "thinking": req.thinking,
        "output_model": req.output_model.__name__,
        "output_schema_sha256": _sha256(schema.encode("utf-8")),
        "content": [_part_summary(p) for p in req.content],
    }


def cassette_key(req: StructuredRequest[Any]) -> str:
    canonical = json.dumps(key_inputs(req), sort_keys=True, ensure_ascii=False)
    return _sha256(canonical.encode("utf-8"))


class RecordReplayProvider:
    def __init__(self, inner: LLMProvider | None, cassette_dir: Path, mode: RecordMode) -> None:
        if mode != "replay" and inner is None:
            raise ConfigError(f"record_mode={mode!r} needs a real provider to call")
        self._inner = inner
        self._dir = Path(cassette_dir)
        self._mode = mode

    def path_for(self, req: StructuredRequest[Any]) -> Path:
        stage = re.sub(r"[^\w.-]", "_", req.stage)  # '/' or ':' would escape the dir on Windows
        return self._dir / f"{stage}-{cassette_key(req)[:16]}.json"

    async def structured[T: BaseModel](self, req: StructuredRequest[T]) -> StructuredResponse[T]:
        if self._mode == "replay":
            return self._load(req)
        assert self._inner is not None  # guaranteed by __init__
        response = await self._inner.structured(req)
        if self._mode == "record":
            self._save(req, response)
        return response

    def _load[T: BaseModel](self, req: StructuredRequest[T]) -> StructuredResponse[T]:
        path = self.path_for(req)
        if not path.is_file():
            raise CassetteMiss(
                f"no cassette for stage={req.stage!r} model={req.model!r} at {path}; "
                "re-record with MEDX_RECORD_MODE=record (prompt, schema or input changed?)"
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return StructuredResponse(
                parsed=req.output_model.model_validate(data["parsed"]),
                call=CallRecord.model_validate(data["call"]),
                raw_text=data["raw_text"],
            )
        except (ValueError, KeyError, TypeError) as exc:  # JSONDecodeError/ValidationError are ValueErrors
            raise CassetteMiss(f"unreadable cassette {path}: {exc}; re-record with MEDX_RECORD_MODE=record") from exc

    def _save(self, req: StructuredRequest[Any], response: StructuredResponse[Any]) -> None:
        path = self.path_for(req)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "key": cassette_key(req),
            "key_inputs": key_inputs(req),
            "parsed": response.parsed.model_dump(mode="json"),
            "call": response.call.model_dump(mode="json"),
            "raw_text": response.raw_text,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("recorded cassette %s", path)
