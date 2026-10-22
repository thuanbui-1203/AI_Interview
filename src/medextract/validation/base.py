"""Validation rule contract.

Rules run on *normalized* fields (``dict[str, FieldResult]``), after code has re-parsed raw
dates/amounts. A rule never raises on missing data: if a field it needs is null, it returns no
issue (missing required fields are reported by a dedicated rule).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from medextract.core.models import FieldResult, ValidationIssue


@dataclass
class RuleContext:
    doc_type: str
    today: date
    language: str | None = None
    currency: str | None = None
    # Reserved for cross-document checks (Challenge 11 claim bundle); unused by single-doc rules.
    claim: Any | None = None
    extras: dict[str, Any] = field(default_factory=dict)


class Rule(Protocol):
    code: str

    def check(self, fields: Mapping[str, FieldResult], ctx: RuleContext) -> list[ValidationIssue]: ...
