import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ingest.ocr import TESSERACT_AVAILABLE, ocr_bytes
from app.ingest.pdf import PdfError, extract_pdf
from app.main import app
from app.pipeline import analyze

ROOT = Path(__file__).resolve().parent.parent
FILES = ROOT / "samples" / "files"
client = TestClient(app)
needs_ocr = pytest.mark.skipif(not TESSERACT_AVAILABLE, reason="tesseract not installed")


def rows(text):
    return {v.test_name: (v.value_raw, v.status.value) for v in analyze(text).values}


BASE = rows((ROOT / "samples" / "01_cbc.txt").read_text())


def test_text_pdf_matches_txt_exactly():
    r = extract_pdf((FILES / "01_cbc.pdf").read_bytes())
    assert r.source == "pdf_text" and r.pages == 1
    assert rows(r.text) == BASE


@needs_ocr
def test_clean_png_ocr_recovers_names_and_values():
    res = ocr_bytes((FILES / "01_cbc.png").read_bytes())
    got = rows(res.text)
    names = set(BASE) & set(got)
    assert len(names) >= 0.9 * len(BASE)
    assert all(got[n][0] == BASE[n][0] for n in names)      # every numeric value identical
    assert res.confidence > 80


@needs_ocr
def test_phone_photo_reports_low_confidence_tokens():
    res = ocr_bytes((FILES / "01_cbc_scan.jpg").read_bytes())
    assert res.low_confidence_tokens
    assert any(any(c.isdigit() for c in t) for t in res.low_confidence_tokens)


@needs_ocr
def test_scanned_pdf_uses_ocr():
    r = extract_pdf((FILES / "02_lipid_scan.pdf").read_bytes())
    assert r.source == "pdf_ocr" and r.ocr_confidence is not None
    assert "Triglycerides" in r.text and "Fasting Blood Sugar" in r.text
    # Known real OCR confusion on this fixture: "HbA1c" -> "HbAlc", "< 5.7" -> "<25.7".
    # This is why extraction never auto-analyzes: the user reviews the text first.


def test_corrupt_pdf_is_friendly():
    with pytest.raises(PdfError):
        extract_pdf(b"%PDF-1.4 garbage that is not a pdf")


def post(name, data, ctype="application/octet-stream"):
    return client.post("/api/extract", files={"file": (name, io.BytesIO(data), ctype)})


def test_api_extract_text_pdf():
    r = post("01_cbc.pdf", (FILES / "01_cbc.pdf").read_bytes(), "application/pdf")
    assert r.status_code == 200 and r.json()["source"] == "pdf_text"


@needs_ocr
def test_api_extract_image():
    r = post("01_cbc_scan.jpg", (FILES / "01_cbc_scan.jpg").read_bytes(), "image/jpeg")
    body = r.json()
    assert r.status_code == 200 and body["source"] == "image_ocr" and 0 < body["ocr_confidence"] <= 100


def test_api_rejects_bad_type():
    assert post("report.txt", b"hello").status_code == 415
    assert post("fake.pdf", b"not really a pdf").status_code == 415     # magic bytes checked


def test_api_rejects_too_large():
    assert post("big.pdf", b"%PDF" + b"0" * (10 * 1024 * 1024)).status_code == 413


def test_api_corrupt_pdf_422():
    r = post("bad.pdf", b"%PDF-1.4 broken")
    assert r.status_code == 422 and "PDF" in r.json()["detail"]


def test_health_reports_ocr():
    assert "ocr_available" in client.get("/api/health").json()


def test_password_protected_pdf_friendly_message():
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, encrypt="secret")
    c.drawString(50, 700, "Hemoglobin 11.2 g/dL 13-17")
    c.save()
    with pytest.raises(PdfError, match="password-protected"):
        extract_pdf(buf.getvalue())
