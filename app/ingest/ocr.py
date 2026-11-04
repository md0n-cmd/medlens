"""OCR for photos / scans (pytesseract). Everything happens in memory."""
from __future__ import annotations

import io
import re
import shutil
from dataclasses import dataclass, field

from PIL import Image, ImageFilter, ImageOps

TESSERACT_AVAILABLE = shutil.which("tesseract") is not None
OCR_MISSING_MSG = "OCR engine not installed: sudo apt install tesseract-ocr"
TESS_CONFIG = "--psm 6 -c preserve_interword_spaces=1"
LOW_CONF = 60


class OcrUnavailable(RuntimeError):
    pass


@dataclass
class OcrResult:
    text: str
    confidence: float                      # mean word confidence, 0-100
    low_confidence_tokens: list[str] = field(default_factory=list)


def preprocess(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    img = img.convert("L")
    if img.width < 2000:
        scale = 2000 / img.width
        img = img.resize((2000, int(img.height * scale)), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=1)
    return img.filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=3))


def _interesting(tok: str) -> bool:
    """Numbers or test-name-like words: the tokens whose misreading would change a result."""
    if re.search(r"\d", tok):
        return True
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z\-/()]{3,}", tok) or re.fullmatch(r"[A-Z]{2,6}", tok))


def ocr_image(img: Image.Image) -> OcrResult:
    if not TESSERACT_AVAILABLE:
        raise OcrUnavailable(OCR_MISSING_MSG)
    import pytesseract

    img = preprocess(img)
    text = pytesseract.image_to_string(img, config=TESS_CONFIG)
    data = pytesseract.image_to_data(img, config=TESS_CONFIG, output_type=pytesseract.Output.DICT)
    confs, low = [], []
    for tok, conf in zip(data["text"], data["conf"]):
        tok, conf = tok.strip(), float(conf)
        if not tok or conf < 0:
            continue
        confs.append(conf)
        if conf < LOW_CONF and _interesting(tok) and tok not in low:
            low.append(tok)
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip("\n")
    return OcrResult(text=text, confidence=round(sum(confs) / len(confs), 1) if confs else 0.0,
                     low_confidence_tokens=low[:40])


def ocr_bytes(data: bytes) -> OcrResult:
    return ocr_image(Image.open(io.BytesIO(data)))
