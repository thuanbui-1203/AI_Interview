"""Document-type plugin registry.

A document type is one module in ``medextract.doctypes`` exposing a module-level ``SPEC``.
``discover()`` imports every module in that package and registers its SPEC, so adding a type
means dropping in one file - the pipeline core never changes. ``register()`` is public so tests
or third parties can add types at runtime.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel

from medextract.errors import ConfigError
from medextract.validation.base import Rule


@dataclass(frozen=True)
class DocTypeSpec:
    name: str  # classification label, e.g. "receipt"
    description: str  # one line, injected into the classifier prompt
    schema: type[BaseModel]  # LLM extraction schema (built from core.fields types)
    required_fields: Sequence[str]  # field names the brief requires (exact names)
    prompt_hints: str = ""  # type-specific extraction guidance (incl. Vietnamese label cues)
    classifier_cues: str = ""  # what distinguishes this type from the others
    rules: Sequence[Rule] = field(default_factory=tuple)
    # Money fields that are magnitudes by definition (a discount, an insurer's share). Documents print
    # them as "-10,000.00" or "(10,000)"; the sign is presentation, so the value is stored as abs().
    magnitude_fields: Sequence[str] = ()
    # Text fields whose value is a normalization of what is printed (an ISO currency code read off a
    # "₫" sign), so the value is not expected verbatim inside the evidence quote.
    inferred_fields: Sequence[str] = ()
    version: str = "1"

    @property
    def output_fields(self) -> list[str]:
        """Public field names, in schema order (excludes extraction metadata)."""
        return [n for n in self.schema.model_fields if n not in META_FIELDS]


# Extraction-schema fields that describe the document rather than being document fields.
META_FIELDS = frozenset({"observed_document_type", "document_language"})


class DocTypeRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, DocTypeSpec] = {}

    def register(self, spec: DocTypeSpec) -> None:
        if spec.name in self._specs:
            raise ConfigError(f"duplicate document type: {spec.name!r}")
        missing = [f for f in spec.required_fields if f not in spec.schema.model_fields]
        if missing:
            raise ConfigError(f"{spec.name}: required fields not in schema: {missing}")
        from medextract.core.schema_lint import lint_schema  # local import: avoids a cycle

        problems = lint_schema(spec.schema)
        if problems:
            raise ConfigError(f"{spec.name}: schema not compatible with structured outputs: {problems}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> DocTypeSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise ConfigError(f"unknown document type: {name!r}") from None

    def names(self) -> list[str]:
        return list(self._specs)

    def all(self) -> list[DocTypeSpec]:
        return list(self._specs.values())

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def discover(self, package: str = "medextract.doctypes") -> DocTypeRegistry:
        pkg = importlib.import_module(package)
        for mod in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
            if mod.name.startswith("_"):
                continue
            module = importlib.import_module(f"{package}.{mod.name}")
            spec = getattr(module, "SPEC", None)
            if isinstance(spec, DocTypeSpec) and spec.name not in self._specs:
                self.register(spec)
        return self


def default_registry() -> DocTypeRegistry:
    return DocTypeRegistry().discover()
