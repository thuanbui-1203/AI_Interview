"""Builders for canned LLM outputs (dicts shaped like the structured-output schemas)."""

from __future__ import annotations

import io
from typing import Any

from PIL import Image

from medextract.core.document import Document, PageImage


def text(value: str | None, *, evidence: str | None = "auto", conf: str = "certain", status: str | None = None, page: int = 1) -> dict:
    if value is None:
        return {"evidence": None, "page": None, "status": status or "not_found", "value": None, "confidence": "none"}
    return {
        "evidence": value if evidence == "auto" else evidence,
        "page": page,
        "status": status or "present",
        "value": value,
        "confidence": conf,
    }


def date_(raw: str | None, value: str | None, *, conf: str = "certain", status: str | None = None, evidence: str = "auto") -> dict:
    if raw is None and value is None:
        return {"evidence": None, "page": None, "status": "not_found", "raw": None, "value": None, "confidence": "none"}
    return {
        "evidence": f"Date: {raw}" if evidence == "auto" else evidence,
        "page": 1,
        "status": status or "present",
        "raw": raw,
        "value": value,
        "confidence": conf,
    }


money = date_  # same shape: evidence, page, status, raw, value, confidence


def choice(value: str | None, raw: str | None = None, conf: str = "certain") -> dict:
    if value is None:
        return {"evidence": None, "page": None, "status": "not_found", "raw": None, "value": None, "confidence": "none"}
    return {"evidence": raw or value, "page": 1, "status": "present", "raw": raw or value, "value": value, "confidence": conf}


def rows(*items: dict, status: str = "present") -> dict:
    return {"status": status, "rows": list(items)}


def receipt_payload(**over: Any) -> dict:
    base = {
        "observed_document_type": "receipt",
        "document_language": "vi",
        "currency": text("VND", evidence="₫"),
        "hospital_name": text("Bệnh viện Đa khoa Hòa Bình"),
        "patient_name": text("NGUYỄN VĂN AN"),
        "date": date_("12/03/2024", "2024-03-12"),
        "items": rows(
            {"evidence": "Khám bệnh 1 150.000 150.000", "page": 1, "description": "Khám bệnh", "quantity": 1, "unit_price": 150000, "total": 150000, "category": "consultation", "confidence": "certain"},
            {"evidence": "Xét nghiệm máu 1 350.000 350.000", "page": 1, "description": "Xét nghiệm máu", "quantity": 1, "unit_price": 350000, "total": 350000, "category": "lab", "confidence": "high"},
        ),
        "grand_total": money("500.000 ₫", 500000.0, evidence="Tổng cộng: 500.000 ₫"),
        "payment_method": choice("cash", "Tiền mặt"),
        "invoice_number": text(None),
        "subtotal": money(None, None),
        "discount_total": money(None, None),
        "tax_amount": money(None, None),
        "insurance_covered_amount": money(None, None),
        "patient_payable": money(None, None),
        "amount_in_words": text(None),
    }
    base.update(over)
    return base


def classification(doc_type: str = "receipt", top: float = 0.95, medical: bool = True) -> dict:
    names = ["receipt", "discharge_summary", "lab_report", "prescription"]
    rest = (1 - top) / 3
    return {
        "cues": "title HÓA ĐƠN, table of charges",
        "is_medical_document": medical,
        "document_language": "vi",
        "scores": {n: (top if n == doc_type else rest) for n in names},
        "document_type": doc_type,
    }


def png_bytes(w: int = 400, h: int = 300) -> bytes:
    img = Image.new("RGB", (w, h), "white")
    for x in range(50, 350):  # some ink so the page is not "blank"
        for y in (100, 101, 150, 151):
            img.putpixel((x, y), (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def make_doc(page_texts: list[str | None] | None = None) -> Document:
    page_texts = page_texts or [None]
    data = png_bytes()
    pages = tuple(
        PageImage(index=i + 1, media_type="image/png", data=data, width=400, height=300, text=t, blur_score=None, blank=False)
        for i, t in enumerate(page_texts)
    )
    has_text = any(t and len(t.strip()) >= 20 for t in page_texts)
    return Document(
        source_name="test.png", sha256="0" * 64, mime="image/png", pages=pages, has_text_layer=has_text, pdf_bytes=None, warnings=()
    )
