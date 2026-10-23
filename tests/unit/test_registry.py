"""A new document type is one spec - no change to the pipeline core."""

import asyncio
from datetime import date

import pytest
from pydantic import Field

from medextract.config import Settings
from medextract.core.fields import DateField, LLMModel, TextField
from medextract.core.pipeline import Extractor
from medextract.core.registry import DocTypeRegistry, DocTypeSpec, default_registry
from medextract.doctypes._common import ExtractionBase
from medextract.errors import ConfigError
from medextract.providers.fake import FakeProvider
from tests.helpers import date_, png_bytes, text


class InsuranceCardExtraction(ExtractionBase):
    insurer_name: TextField
    member_name: TextField
    card_number: TextField
    valid_to: DateField


CARD = DocTypeSpec(
    name="insurance_card",
    description="Health insurance membership card.",
    schema=InsuranceCardExtraction,
    required_fields=("insurer_name", "member_name", "card_number"),
    prompt_hints="INSURANCE CARD\n- card_number: as printed, keep spaces.",
)


def test_a_new_document_type_runs_end_to_end_without_core_changes():
    registry = default_registry()
    registry.register(CARD)
    names = ["receipt", "discharge_summary", "lab_report", "prescription", "insurance_card"]
    cls = {"cues": "card", "is_medical_document": True, "document_language": "en",
           "scores": {n: (0.96 if n == "insurance_card" else 0.01) for n in names}, "document_type": "insurance_card"}  # fmt: skip
    payload = {
        "observed_document_type": "insurance_card",
        "document_language": "en",
        "insurer_name": text("Lotus Health Insurance"),
        "member_name": text("Tran Minh"),
        "card_number": text("LHI 0042 7781"),
        "valid_to": date_("31/12/2027", "2027-12-31"),
    }
    provider = FakeProvider({"classify": [cls], "extract": [payload]})
    ex = Extractor(settings=Settings(), provider=provider, registry=registry, today=lambda: date(2026, 1, 1))
    out = asyncio.run(ex.extract_bytes(png_bytes(), "card.png")).to_public()

    assert out["document_type"] == "insurance_card"
    assert out["confidence"] > 0.9 and not any(i["code"] == "CLASSIFICATION_DISAGREEMENT" for i in out["validation_errors"])
    assert out["fields"]["card_number"]["value"] == "LHI 0042 7781"
    assert out["fields"]["valid_to"]["value"] == "2027-12-31"
    assert "insurance_card" in provider.requests[0].system  # the classifier prompt lists the new type
    assert "INSURANCE CARD" in provider.requests[1].system  # and the extractor got its hints


def test_duplicate_type_is_rejected():
    reg = DocTypeRegistry()
    reg.register(CARD)
    with pytest.raises(ConfigError, match="duplicate"):
        reg.register(CARD)


def test_required_field_must_exist_in_schema():
    bad = DocTypeSpec(name="x", description="x", schema=InsuranceCardExtraction, required_fields=("nope",))
    with pytest.raises(ConfigError, match="required fields not in schema"):
        DocTypeRegistry().register(bad)


class WithDefault(LLMModel):
    a: TextField
    b: str | None = None  # optional property: the model could silently drop it


class WithConstraint(LLMModel):
    n: int = Field(ge=1)  # structured outputs cannot enforce minimum


class Node(LLMModel):
    child: "Node | None"


@pytest.mark.parametrize(
    ("schema", "match"),
    [(WithDefault, "must all be required"), (WithConstraint, "unsupported keyword"), (Node, "recursive")],
)
def test_schema_lint_rejects_unsafe_schemas(schema, match):
    spec = DocTypeSpec(name="x", description="x", schema=schema, required_fields=())
    with pytest.raises(ConfigError, match=match):
        DocTypeRegistry().register(spec)


def test_builtin_types_are_discovered():
    assert default_registry().names() == ["discharge_summary", "lab_report", "prescription", "receipt"]
