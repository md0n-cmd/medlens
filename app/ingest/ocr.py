"""OCR for photos / scans (pytesseract). Everything happens in memory.

Pipeline: EXIF-transpose -> grayscale -> background normalisation (divide by a
heavy blur) -> deskew (projection-profile search) -> upscale -> several variants
(normalised gray, Otsu, adaptive threshold; psm 6 / psm 4) OCR'd in parallel ->
the variant that yields the most parsed lab rows wins (mean confidence breaks ties).
Text is rebuilt from word boxes so table columns keep 2+ space gaps.
Values are never changed: suspected digit confusions are only *suggested*.
"""
from __future__ import annotations

import io
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageFilter, ImageOps

TESSERACT_AVAILABLE = shutil.which("tesseract") is not None
OCR_MISSING_MSG = "OCR engine not installed: sudo apt install tesseract-ocr"
LOW_CONF = 60
TARGET_WIDTH = 2000


class OcrUnavailable(RuntimeError):
    pass


@dataclass
class OcrResult:
    text: str
    confidence: float                      # mean word confidence, 0-100
    low_confidence_tokens: list[str] = field(default_factory=list)
    rows_found: int = 0
    variant: str = ""


# ------------------------------------------------------------ image processing
def normalize_background(gray: Image.Image) -> Image.Image:
    """Divide by a heavily blurred copy: removes paper tint, vignetting and soft shadows."""
    small = gray.resize((max(1, gray.width // 8), max(1, gray.height // 8)), Image.BILINEAR)
    bg = small.filter(ImageFilter.GaussianBlur(6)).resize(gray.size, Image.BILINEAR)
    a = np.asarray(gray, dtype=np.float32)
    b = np.maximum(np.asarray(bg, dtype=np.float32), 1.0)
    out = np.clip(a / b * 255.0, 0, 255).astype(np.uint8)
    return ImageOps.autocontrast(Image.fromarray(out), cutoff=1)


def _ink(gray: Image.Image) -> np.ndarray:
    a = np.asarray(gray, dtype=np.uint8)
    return (a < otsu_level(a)).astype(np.float32)


def estimate_skew(gray: Image.Image) -> float:
    """Projection-profile search: the angle whose row sums are 'spikiest' (text lines aligned)."""
    w = 700
    small = gray.resize((w, max(1, int(gray.height * w / gray.width))), Image.BILINEAR)

    def score(angle: float) -> float:
        rot = small.rotate(angle, resample=Image.BILINEAR, expand=False, fillcolor=255)
        return float(np.var(_ink(rot).sum(axis=1)))

    best = max(np.arange(-5, 5.01, 0.5), key=score)
    fine = max(np.arange(best - 0.5, best + 0.51, 0.1), key=score)
    return round(float(fine), 2)


def otsu_level(a: np.ndarray) -> int:
    hist = np.bincount(a.ravel(), minlength=256).astype(np.float64)
    total = a.size
    sum_all = np.dot(np.arange(256), hist)
    w0 = np.cumsum(hist)
    s0 = np.cumsum(np.arange(256) * hist)
    w1 = total - w0
    valid = (w0 > 0) & (w1 > 0)
    m0 = np.where(valid, s0 / np.maximum(w0, 1), 0)
    m1 = np.where(valid, (sum_all - s0) / np.maximum(w1, 1), 0)
    between = np.where(valid, w0 * w1 * (m0 - m1) ** 2, 0)
    return int(np.argmax(between))


def binarize_otsu(gray: Image.Image) -> Image.Image:
    a = np.asarray(gray, dtype=np.uint8)
    return Image.fromarray(np.where(a > otsu_level(a), 255, 0).astype(np.uint8))


def binarize_adaptive(gray: Image.Image, radius: int = 15, c: int = 12) -> Image.Image:
    mean = np.asarray(gray.filter(ImageFilter.BoxBlur(radius)), dtype=np.int16)
    a = np.asarray(gray, dtype=np.int16)
    return Image.fromarray(np.where(a > mean - c, 255, 0).astype(np.uint8))


def preprocess(img: Image.Image) -> tuple[Image.Image, float]:
    img = ImageOps.exif_transpose(img).convert("L")
    norm = normalize_background(img)
    angle = estimate_skew(norm)
    if abs(angle) >= 0.2:
        norm = norm.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
    if norm.width < TARGET_WIDTH:
        norm = norm.resize((TARGET_WIDTH, int(norm.height * TARGET_WIDTH / norm.width)), Image.LANCZOS)
    norm = norm.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=3))
    return norm, angle


# ------------------------------------------------------------ OCR + text rebuild
JUNK_EDGE = re.compile(r"^[^\w<>≤≥*%/+]+$")


def _rebuild(data: dict) -> tuple[str, list[tuple[str, float]]]:
    """Lines from word boxes; drop low-confidence pure-punctuation noise at line edges."""
    lines: dict[tuple, list[int]] = {}
    for i, tok in enumerate(data["text"]):
        if tok.strip() and float(data["conf"][i]) >= 0:
            lines.setdefault((data["block_num"][i], data["par_num"][i], data["line_num"][i]), []).append(i)
    widths = [data["width"][i] / max(1, len(data["text"][i])) for idx in lines.values() for i in idx]
    cw = float(np.median(widths)) if widths else 10.0
    left0 = min((data["left"][i] for idx in lines.values() for i in idx), default=0)
    out, words = [], []
    for key in sorted(lines, key=lambda k: (min(data["top"][i] for i in lines[k]), k)):
        idx = sorted(lines[key], key=lambda i: data["left"][i])
        def junk(i: int) -> bool:
            t, cf = data["text"][i].strip(), float(data["conf"][i])
            return cf < LOW_CONF and (bool(JUNK_EDGE.match(t)) or (len(t) <= 2 and t.islower()))
        while idx and junk(idx[0]):
            idx.pop(0)
        while idx and junk(idx[-1]):
            idx.pop()
        if not idx:
            continue
        line, prev = " " * max(0, round((data["left"][idx[0]] - left0) / cw)), None
        for i in idx:
            if prev is not None:
                line += " " * max(1, round((data["left"][i] - prev) / cw))
            line += data["text"][i].strip()
            prev = data["left"][i] + data["width"][i]
            words.append((data["text"][i].strip(), float(data["conf"][i])))
        out.append(line.rstrip())
    return "\n".join(out), words


def _run(img: Image.Image, psm: int) -> tuple[str, list[tuple[str, float]]]:
    import pytesseract
    data = pytesseract.image_to_data(img, config=f"--psm {psm} -c preserve_interword_spaces=1",
                                     output_type=pytesseract.Output.DICT)
    return _rebuild(data)


def _interesting(tok: str) -> bool:
    if re.search(r"\d", tok):
        return True
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z\-/()]{3,}", tok) or re.fullmatch(r"[A-Z]{2,6}", tok))


CONFUSABLE = re.compile(r"^[<>]?(?=[\dOoIl.,]*\d)(?=.*[OoIl])[\dOoIl][\dOoIl.,]*$")


def suggest_digit_fixes(text: str) -> list[str]:
    """'4.1O' -> '4.10?' style hints for numeric cells. The text itself is never changed."""
    out = []
    for tok in re.findall(r"(?<!\S)\S+(?!\S)", text):
        if CONFUSABLE.match(tok):
            fixed = tok.translate(str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1"}))
            hint = f"{tok} → {fixed}?"
            if hint not in out:
                out.append(hint)
    return out


def _rows(text: str) -> int:
    from app.pipeline import analyze
    return sum(1 for v in analyze(text).values if v.reference is not None)


def ocr_image(img: Image.Image) -> OcrResult:
    if not TESSERACT_AVAILABLE:
        raise OcrUnavailable(OCR_MISSING_MSG)
    base, _angle = preprocess(img)
    variants = {
        "normalised/psm6": (base, 6),
        "otsu/psm6": (binarize_otsu(base), 6),
        "adaptive/psm6": (binarize_adaptive(base), 6),
        "normalised/psm4": (base, 4),
    }
    with ThreadPoolExecutor(max_workers=len(variants)) as pool:
        futures = {name: pool.submit(_run, im, psm) for name, (im, psm) in variants.items()}
        results = {name: f.result() for name, f in futures.items()}

    def rank(name: str):
        text, words = results[name]
        confs = [c for _, c in words]
        return (_rows(text), sum(confs) / len(confs) if confs else 0.0)

    best = max(results, key=rank)
    text, words = results[best]
    rows, conf = rank(best)
    low = suggest_digit_fixes(text)
    for tok, cf in words:
        if cf < LOW_CONF and _interesting(tok) and tok not in low:
            low.append(tok)
    return OcrResult(text=text.strip("\n"), confidence=round(conf, 1), low_confidence_tokens=low[:40],
                     rows_found=rows, variant=best)


def ocr_bytes(data: bytes) -> OcrResult:
    return ocr_image(Image.open(io.BytesIO(data)))
