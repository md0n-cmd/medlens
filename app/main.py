"""MedLens FastAPI app. Report text is processed in memory only and never stored or logged."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

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
    return {"status": "ok", "llm_fallback": _explainer() is not None}


@app.get("/api/samples")
def samples() -> list[dict]:
    out = []
    for p in sorted(SAMPLES.glob("*.txt")):
        out.append({"id": p.stem, "title": p.stem.replace("_", " ").title(), "text": p.read_text(encoding="utf-8")})
    return out


@app.post("/api/analyze", response_model=AnalysisResult)
def analyze_endpoint(req: AnalyzeRequest) -> AnalysisResult:
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Report text is empty.")
    return analyze(req.text, explain_unknown=_explainer())


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND)
