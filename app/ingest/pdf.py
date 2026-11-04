"""Text extraction from PDFs: text layer first (layout kept), OCR for scanned pages."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from app.ingest.ocr import OcrUnavailable, ocr_image

MIN_PAGE_CHARS = 20
OCR_DPI = 300


class PdfError(ValueError):
    pass


@dataclass
class PdfResult:
    text: str
    source: str                       # "pdf_text" | "pdf_ocr"
    pages: int
    ocr_confidence: float | None = None
    low_confidence_tokens: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _clean_layout(text: str) -> str:
    lines = [ln.rstrip() for ln in text.splitlines()]
    # strip the common left margin that layout mode adds, collapse blank-line runs
    indents = [len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()]
    cut = min(indents) if indents else 0
    out = "\n".join(ln[cut:] for ln in lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip("\n")


def _page_text(page) -> str:
    """Rebuild lines from word positions so table columns keep 2+ space gaps."""
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=1.5)
    if not words:
        return ""
    cws = sorted((w["x1"] - w["x0"]) / max(1, len(w["text"])) for w in words)
    cw = cws[len(cws) // 2] or 5.0
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if rows and abs(rows[-1][0]["top"] - w["top"]) <= 3:
            rows[-1].append(w)
        else:
            rows.append([w])
    left = min(w["x0"] for w in words)
    out = []
    for row in rows:
        row.sort(key=lambda w: w["x0"])
        line, prev = " " * max(0, round((row[0]["x0"] - left) / cw)), None
        for w in row:
            if prev is not None:
                line += " " * max(1, round((w["x0"] - prev) / cw))
            line += w["text"]
            prev = w["x1"]
        out.append(line.rstrip())
    return "\n".join(out)


def extract_pdf(data: bytes) -> PdfResult:
    import pdfplumber

    try:
        pdf = pdfplumber.open(io.BytesIO(data))
        pages = list(pdf.pages)
    except Exception as exc:
        name = (exc.__class__.__name__ + " " + " ".join(type(a).__name__ for a in exc.args)).lower()
        if "password" in name or "encrypt" in name:
            raise PdfError("This PDF is password-protected. Please open it, remove the password, and upload it again.")
        raise PdfError("This file could not be read as a PDF. It may be damaged; please try another copy.")
    texts, ocr_pages, confs, low, warnings = [], 0, [], [], []
    with pdf:
        for i, page in enumerate(pages, start=1):
            try:
                txt = _page_text(page)
            except Exception:
                txt = ""
            if len(txt.strip()) >= MIN_PAGE_CHARS:
                texts.append(_clean_layout(txt))
                continue
            # scanned page -> render + OCR
            import pypdfium2 as pdfium
            try:
                doc = pdfium.PdfDocument(data)
                img = doc[i - 1].render(scale=OCR_DPI / 72).to_pil()
            except Exception:
                warnings.append(f"Page {i} could not be rendered for OCR.")
                continue
            try:
                res = ocr_image(img)
            except OcrUnavailable:
                raise
            ocr_pages += 1
            texts.append(res.text)
            confs.append(res.confidence)
            low.extend(t for t in res.low_confidence_tokens if t not in low)
    if not any(t.strip() for t in texts):
        raise PdfError("No text could be found in this PDF.")
    source = "pdf_ocr" if ocr_pages else "pdf_text"
    if ocr_pages and ocr_pages < len(pages):
        warnings.append(f"{ocr_pages} of {len(pages)} pages were scanned images and were read with OCR.")
    return PdfResult(text="\n\n".join(texts), source=source, pages=len(pages),
                     ocr_confidence=round(sum(confs) / len(confs), 1) if confs else None,
                     low_confidence_tokens=low[:40], warnings=warnings)
