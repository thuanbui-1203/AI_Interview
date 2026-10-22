"""The public facade: ``Extractor``.

ingest -> classify -> extract -> normalize -> validate -> score -> PipelineResult.
Everything except the provider is deterministic, which is what makes the pipeline testable with a
FakeProvider or recorded cassettes.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date

from medextract import confidence as C
from medextract.config import PricingTable, Settings
from medextract.core import steps
from medextract.core.document import Document
from medextract.core.models import ClassificationInfo, PipelineResult, RunMeta, ValidationIssue
from medextract.core.normalize import normalize
from medextract.core.registry import DocTypeRegistry, default_registry
from medextract.errors import ConfigError
from medextract.ingest import IngestSettings, load_document
from medextract.providers.base import LLMProvider
from medextract.validation.base import RuleContext
from medextract.validation.rules import RequiredFields, rules_for


class Extractor:
    def __init__(
        self,
        settings: Settings | None = None,
        provider: LLMProvider | None = None,
        registry: DocTypeRegistry | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.settings = settings or Settings()
        self.registry = registry or default_registry()
        self.provider = provider or build_provider(self.settings)
        self.today = today

    def ingest_settings(self) -> IngestSettings:
        s = self.settings
        return IngestSettings(
            max_file_mb=s.max_file_mb,
            max_pages=s.max_pages,
            pdf_dpi=s.pdf_dpi,
            image_max_px=s.image_max_px,
            max_megapixels=s.image_max_megapixels,
        )

    async def extract_bytes(
        self, data: bytes, source_name: str | None = None, expected_type: str | None = None
    ) -> PipelineResult:
        t0 = time.perf_counter()
        doc = load_document(data, source_name=source_name, settings=self.ingest_settings())
        ingest_ms = _ms(t0)
        result = await self.extract_document(doc, expected_type=expected_type)
        result.meta.timings_ms = {"ingest": ingest_ms, **result.meta.timings_ms}
        return result

    async def extract_document(self, doc: Document, expected_type: str | None = None) -> PipelineResult:
        if self.settings.strategy != "two_stage":
            raise ConfigError(f"strategy {self.settings.strategy!r} is not implemented yet")
        meta = RunMeta(
            source_name=doc.source_name,
            sha256=doc.sha256,
            mime=doc.mime,
            page_count=len(doc.pages),
            has_text_layer=doc.has_text_layer,
            strategy=self.settings.strategy,
        )

        # 1. classify (skipped when the caller already knows the type, e.g. a claim form slot)
        t = time.perf_counter()
        if expected_type:
            if expected_type not in self.registry:
                raise ConfigError(f"unknown expected_type {expected_type!r}; known: {self.registry.names()}")
            cls = ClassificationInfo(
                document_type=expected_type, confidence=1.0, scores={expected_type: 1.0}, cues="provided by caller"
            )
        else:
            cls, call = await steps.classify(doc, self.registry, self.provider, self.settings)
            meta.calls.append(call)
        meta.timings_ms["classify"] = _ms(t)
        spec = self.registry.get(cls.document_type)
        meta.spec_version = spec.version

        # 2. extract with the type's own schema and hints
        t = time.perf_counter()
        extraction, call, meta.prompt_version = await steps.extract(doc, spec, self.provider, self.settings)
        meta.calls.append(call)
        meta.timings_ms["extract"] = _ms(t)

        # 3. normalize + score + validate (pure code)
        t = time.perf_counter()
        norm = normalize(extraction, spec, doc)
        doc_conf, issues = C.document_confidence(cls, norm.observed_type)
        issues += norm.issues
        ctx = RuleContext(doc_type=spec.name, today=self.today(), language=norm.language, currency=norm.currency)
        for rule in [RequiredFields(spec.required_fields), *rules_for(spec.name), *spec.rules]:
            issues += _run_rule(rule, norm.fields, ctx)
        C.apply_issue_caps(norm.fields, issues)
        meta.timings_ms["postprocess"] = _ms(t)

        return PipelineResult(
            document_type=spec.name,
            confidence=doc_conf,
            fields=norm.fields,
            validation_errors=issues,
            classification=cls,
            meta=meta,
        )


def _run_rule(rule, fields, ctx) -> list[ValidationIssue]:
    """A buggy rule must not take the whole extraction down; surface it as an info issue."""
    try:
        return rule.check(fields, ctx)
    except Exception as exc:
        return [ValidationIssue(code="RULE_ERROR", severity="info", message=f"{rule.code}: {exc!r}")]


def build_provider(settings: Settings) -> LLMProvider:
    from medextract.providers.anthropic_provider import AnthropicProvider
    from medextract.providers.cassette import RecordReplayProvider

    inner = None if settings.record_mode == "replay" else AnthropicProvider(settings, PricingTable())
    if settings.record_mode == "off":
        return inner
    return RecordReplayProvider(inner, settings.cassette_dir, settings.record_mode)


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)
