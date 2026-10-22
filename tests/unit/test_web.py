from datetime import date

from fastapi.testclient import TestClient

from medextract.config import Settings
from medextract.core.pipeline import Extractor
from medextract.errors import ModelRefusal
from medextract.interfaces.web.app import app, get_extractor
from medextract.providers.fake import FakeProvider
from tests.helpers import classification, png_bytes, receipt_payload


def client_with(handler) -> TestClient:
    ex = Extractor(settings=Settings(), provider=FakeProvider(handler), today=lambda: date(2024, 6, 1))
    app.dependency_overrides[get_extractor] = lambda: ex
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_index_and_types():
    c = client_with({})
    assert "medextract" in c.get("/").text
    t = c.get("/api/types").json()
    assert t["receipt"]["required"][:2] == ["hospital_name", "patient_name"]


def test_extract_public_and_verbose():
    c = client_with({"classify": [classification(), classification()], "extract": [receipt_payload(), receipt_payload()]})
    r = c.post("/api/extract", files={"file": ("bill.png", png_bytes(), "image/png")})
    assert r.status_code == 200
    assert set(r.json()) == {"document_type", "confidence", "fields", "validation_errors"}
    r = c.post("/api/extract", files={"file": ("bill.png", png_bytes(), "image/png")}, data={"verbose": "true"})
    assert "meta" in r.json() and r.json()["fields"]["patient_name"]["evidence"]


def test_expected_type_form_field():
    c = client_with({"extract": [receipt_payload()]})
    r = c.post("/api/extract", files={"file": ("bill.png", png_bytes(), "image/png")}, data={"type": "receipt"})
    assert r.status_code == 200 and r.json()["confidence"] == 1.0


def test_domain_errors_map_to_http_status():
    c = client_with({"classify": [ModelRefusal("nope")]})
    r = c.post("/api/extract", files={"file": ("bill.png", png_bytes(), "image/png")})
    assert r.status_code == 422 and r.json()["error"] == "model_refusal"
    r = c.post("/api/extract", files={"file": ("notes.txt", b"hello world", "text/plain")})
    assert r.status_code == 415 and r.json()["error"] == "unsupported_file_type"
