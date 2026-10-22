"""In-memory provider for unit tests: canned answers, no network, every request recorded."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from medextract.core.models import CallRecord
from medextract.providers.base import StructuredRequest, StructuredResponse

# A canned outcome: the parsed object (or a dict to validate into the requested schema),
# or an exception to raise - which is how tests simulate refusals, truncation and outages.
Outcome = BaseModel | Mapping[str, Any] | Exception
Handler = Callable[[StructuredRequest[Any]], Outcome]


class FakeProvider:
    """``handler`` is either a function of the request, or a queue of outcomes per stage.

    The per-stage queue form (``{"classify": [a], "extract": [b, c]}``) is the common case:
    answers are consumed in order, and running out is a test bug, so it raises loudly.
    """

    def __init__(self, handler: Handler | Mapping[str, Sequence[Outcome]]) -> None:
        if callable(handler):
            self._handler: Handler = handler
        else:
            queues = {stage: list(items) for stage, items in handler.items()}
            self._handler = lambda req: _pop(queues, req.stage)
        self.requests: list[StructuredRequest[Any]] = []

    async def structured[T: BaseModel](self, req: StructuredRequest[T]) -> StructuredResponse[T]:
        self.requests.append(req)
        outcome = self._handler(req)
        if isinstance(outcome, Exception):
            raise outcome
        parsed = outcome if isinstance(outcome, req.output_model) else req.output_model.model_validate(outcome)
        call = CallRecord(
            stage=req.stage,
            model=req.model,
            stop_reason="end_turn",
            request_id=f"fake-{len(self.requests)}",
        )
        return StructuredResponse(parsed=parsed, call=call, raw_text=parsed.model_dump_json())


def _pop(queues: dict[str, list[Outcome]], stage: str) -> Outcome:
    queue = queues.get(stage)
    if not queue:
        raise AssertionError(f"FakeProvider has no canned response left for stage {stage!r}")
    return queue.pop(0)
