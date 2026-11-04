"""MedLens FastAPI app. Report text is processed in memory only and never stored or logged."""
from __future__ import annotations

import os
from pathlib import Path

from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.ingest.ocr import OCR_MISSING_MSG, TESSERACT_AVAILABLE, OcrUnavailable, ocr_bytes
from app.ingest.pdf import PdfError, extract_pdf

from app.models import AnalysisResult, AnalyzeRequest
from app.pipeline import analyze

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
FRONTEND = ROOT / "frontend" / "index.html"

app = FastAPI(title="MedLens", version="1.0.0",
              description="Medical report explanation & information navigator (informational only, not diagnosis).")


def _explainer():
    if os.getenv("USE_LLM") == "1" and os.getenv("ANTHROPIC_API_KEY"):
        from app.llm import explain_unknown_term
        return explain_unknown_term
    return None


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "llm_fallback": _explainer() is not None, "ocr_available": TESSERACT_AVAILABLE}


@app.get("/api/samples")
def samples() -> list[dict]:
    out = []
    for p in sorted(SAMPLES.glob("*.txt")):
        out.append({"id": p.stem, "title": p.stem.replace("_", " ").title(), "text": p.read_text(encoding="utf-8")})
    return out


@app.get("/api/terms")
def terms(q: str = "") -> list[dict]:
    from app.glossary.search import search_terms
    if not q.strip():
        raise HTTPException(status_code=400, detail="Type a term to search.")
    return search_terms(q)


@app.post("/api/analyze", response_model=AnalysisResult)
def analyze_endpoint(req: AnalyzeRequest) -> AnalysisResult:
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Report text is empty.")
    return analyze(req.text, explain_unknown=_explainer())


MAX_UPLOAD = 10 * 1024 * 1024
ALLOWED_EXT = {".pdf": "pdf", ".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp"}


class ExtractResult(BaseModel):
    text: str
    source: str
    pages: int
    ocr_confidence: Optional[float] = None
    low_confidence_tokens: list[str] = []
    warnings: list[str] = []
    rows_found: int = 0


def sniff(data: bytes) -> Optional[str]:
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


@app.post("/api/extract", response_model=ExtractResult)
async def extract_endpoint(file: UploadFile = File(...)) -> ExtractResult:
    """Extract text for REVIEW. Never auto-analyzes: OCR can misread numbers."""
    ext = Path(file.filename or "").suffix.lower()
    data = await file.read(MAX_UPLOAD + 1)          # in memory only, never written to disk
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File is too large (max 10 MB).")
    kind = sniff(data)
    if ext not in ALLOWED_EXT or kind is None or (ALLOWED_EXT[ext] != kind and {ALLOWED_EXT[ext], kind} != {"jpeg"}):
        raise HTTPException(415, "Unsupported file. Please upload a PDF, PNG, JPG or WEBP file.")
    try:
        if kind == "pdf":
            r = extract_pdf(data)
            return ExtractResult(text=r.text, source=r.source, pages=r.pages, ocr_confidence=r.ocr_confidence,
                                 low_confidence_tokens=r.low_confidence_tokens, warnings=r.warnings,
                                 rows_found=_rows_found(r.text))
        res = ocr_bytes(data)
    except OcrUnavailable:
        raise HTTPException(503, OCR_MISSING_MSG)
    except PdfError as exc:
        raise HTTPException(422, str(exc))
    except Exception:
        raise HTTPException(422, "This image could not be read. Please try a clearer photo or another file.")
    warnings = [] if res.confidence >= 70 else ["The scan quality is low. Please compare every number with your paper report."]
    return ExtractResult(text=res.text, source="image_ocr", pages=1, ocr_confidence=res.confidence,
                         low_confidence_tokens=res.low_confidence_tokens, warnings=warnings,
                         rows_found=res.rows_found)


def _rows_found(text: str) -> int:
    return sum(1 for v in analyze(text).values if v.reference is not None)


@app.get("/api/sample-files/{name}", include_in_schema=False)
def sample_file(name: str) -> FileResponse:
    p = (SAMPLES / "files" / name).resolve()
    if p.parent != (SAMPLES / "files").resolve() or not p.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(p)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND)
