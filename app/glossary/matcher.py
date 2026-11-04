"""Glossary term detection with character spans.

* longest-match-first: all aliases go into one alternation sorted by length, so
  "Direct Bilirubin" wins over "Bilirubin" at the same position.
* word boundaries: a match may not be glued to letters/digits ("ALT" never
  matches inside "SALT", "Hb" never inside "HbA1c").
* short aliases (<= 3 chars, abbreviations) are case-sensitive (as written or
  ALL CAPS) to avoid matching ordinary words; longer ones are case-insensitive.
* flexible separators: "Bilirubin, Direct" / "Bilirubin (Direct)" / "Bilirubin - Direct".
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from app.models import Span, TermMatch, UnrecognizedTerm

TERMS_PATH = Path(__file__).with_name("terms.json")


@lru_cache(maxsize=1)
def load_glossary() -> dict:
    data = json.loads(TERMS_PATH.read_text(encoding="utf-8"))
    return {t["id"]: t for t in data["terms"]}


def _alias_pattern(alias: str) -> str:
    parts = re.split(r"[\s,()\-]+", alias.strip())
    parts = [re.escape(p) for p in parts if p]
    body = r"[\s,()\-]*".join(parts) if len(parts) > 1 else re.escape(alias.strip())
    if len(alias.strip()) <= 3:
        up = re.escape(alias.strip().upper())
        return f"(?:{re.escape(alias.strip())}|{up})" if up != re.escape(alias.strip()) else f"(?:{body})"
    return f"(?i:{body})"


@lru_cache(maxsize=1)
def _compiled() -> tuple[re.Pattern, list[str]]:
    gl = load_glossary()
    seen: dict[str, str] = {}
    for tid, t in gl.items():
        for a in [t["term"], *t.get("aliases", [])]:
            key = a.strip().lower()
            seen.setdefault(key, tid)   # first definition wins on duplicate aliases
    items = sorted(seen.items(), key=lambda kv: len(kv[0]), reverse=True)
    # Need original-case alias for short ones: rebuild from glossary
    originals: dict[str, str] = {}
    for tid, t in gl.items():
        for a in [t["term"], *t.get("aliases", [])]:
            originals.setdefault(a.strip().lower(), a.strip())
    group_ids, alts = [], []
    for i, (key, tid) in enumerate(items):
        alts.append(f"(?P<g{i}>{_alias_pattern(originals[key])})")
        group_ids.append(tid)
    pat = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(alts) + r")(?![A-Za-z0-9])")
    return pat, group_ids


def _resolve_possibilities(entry: dict, context: str) -> tuple[bool, list[str]]:
    poss = entry.get("possibilities")
    if not poss:
        return False, []
    ctx = context.lower()
    hits = [p for p in poss if any(re.search(rf"\b{re.escape(k)}\b", ctx) for k in p["context_keywords"])]
    if len(hits) == 1:
        return False, [hits[0]["meaning"]]
    return True, [p["meaning"] for p in poss]


def find_terms(text: str) -> list[TermMatch]:
    pat, group_ids = _compiled()
    gl = load_glossary()
    out: list[TermMatch] = []
    for m in pat.finditer(text):
        gi = int(m.lastgroup[1:])
        tid = group_ids[gi]
        e = gl[tid]
        ambiguous, poss = _resolve_possibilities(e, text)
        expl = e["plain_explanation"]
        if poss and not ambiguous:
            expl = f"In this report, {e['term']} most likely means: {poss[0]}. " + expl
        out.append(TermMatch(
            id=f"t{len(out)}", term_id=tid, term=e["term"], matched_text=m.group(0),
            span=Span(start=m.start(), end=m.end(), text=m.group(0)), category=e["category"],
            plain_explanation=expl, what_it_is_not=e.get("what_it_is_not"),
            source_note=e.get("source_note"), ambiguous=ambiguous, possibilities=poss,
        ))
    return out


# --------------------------------------------------------- unknown terms
ABBREV_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{1,4}(?![A-Za-z0-9])")  # 2-5 chars
SUFFIX_RE = re.compile(
    r"(?<![A-Za-z])[A-Za-z]{3,}(?:itis|aemia|emia|osis|iasis|megaly|ectomy|opathy|plasia|algia|ectasia|uria|penia|cytosis)(?![A-Za-z])",
    re.I,
)
STOPWORDS = {
    # report furniture / units / common caps words
    "REPORT", "PATIENT", "NAME", "AGE", "SEX", "MALE", "FEMALE", "DATE", "TEST", "TESTS", "RESULT", "RESULTS",
    "UNIT", "UNITS", "REFERENCE", "RANGE", "LAB", "SAMPLE", "SYNTHETIC", "NOT", "REAL", "DATA", "DR", "MD",
    "MBBS", "DNB", "DMRD", "ID", "NO", "OF", "AND", "THE", "END", "PAGE", "AM", "PM", "IU", "UL", "MG", "DL",
    "ML", "FL", "PG", "MM", "HR", "HPF", "LPF", "MIU", "UIU", "NG", "MMOL", "MEQ", "GM", "KG", "CM", "YRS",
    "YEARS", "TO", "OR", "IN", "ON", "AT", "BY", "FOR", "WITH", "IS", "ARE", "NIL", "NA", "NAD", "OK", "USG",
    "CT", "MRI", "XRAY", "X", "UHID", "MRN", "REF", "LTD", "PVT", "INDIA", "ROAD", "H", "L", "HH", "LL",
    "ABDOMEN", "PELVIS", "WHOLE", "FINDINGS", "IMPRESSION", "ADVICE", "NOTE", "REMARKS", "COMMENT", "COMMENTS",
    "HIGH", "LOW", "NORMAL", "ABNORMAL", "NEGATIVE", "POSITIVE", "ABSENT", "PRESENT", "TRACE", "CLEAR",
    "EDTA", "LIVER", "KIDNEY", "OPINION", "CONCLUSION", "COMPLETE", "BLOOD", "COUNT", "PROFILE", "PANEL",
    "URINE", "ROUTINE", "EXAMINATION", "THYROID", "FUNCTION", "LIPID", "DIABETES", "SERUM", "PLASMA",
    "METHOD", "SPECIMEN", "CLINICAL", "HISTORY", "TECHNIQUE", "SUMMARY", "FINAL", "PHYSICAL", "CHEMICAL",
    "MICROSCOPIC", "MICROSCOPY", "SONOGRAPHY", "ULTRASOUND", "SCAN", "STUDY", "CENTRE", "CENTER",
    "DIAGNOSTICS", "LABORATORY", "LABORATORIES", "HOSPITAL", "CLINIC", "DEPARTMENT", "PATHOLOGY",
    "BIOCHEMISTRY", "HAEMATOLOGY", "HEMATOLOGY", "IMMUNOLOGY", "SEROLOGY", "RADIOLOGY", "MEDLENS",
    "CALCULATED", "OBSERVED", "VALUE", "VALUES", "BIOLOGICAL", "INTERVAL", "STATUS", "FLAG",
}


def find_unrecognized(text: str, known_spans: Iterable[tuple[int, int]]) -> list[UnrecognizedTerm]:
    """Medical-looking tokens with no glossary entry. We never guess a meaning."""
    covered = sorted(known_spans)

    def inside(s: int, e: int) -> bool:
        return any(a <= s and e <= b for a, b in covered)

    out: dict[str, UnrecognizedTerm] = {}
    # skip ALL-CAPS header lines (e.g. "COMPLETE BLOOD COUNT") for the abbreviation rule
    caps_lines = []
    pos = 0
    for ln in text.split("\n"):
        letters = [c for c in ln if c.isalpha()]
        if len(letters) > 8 and all(c.isupper() for c in letters):
            caps_lines.append((pos, pos + len(ln)))
        pos += len(ln) + 1

    def in_caps_line(s: int) -> bool:
        return any(a <= s < b for a, b in caps_lines)

    for m in ABBREV_RE.finditer(text):
        tok = m.group(0)
        if tok in STOPWORDS or tok.isdigit() or inside(m.start(), m.end()) or in_caps_line(m.start()):
            continue
        key = tok
        if key not in out:
            out[key] = UnrecognizedTerm(id=f"u{len(out)}", text=tok, span=Span(start=m.start(), end=m.end(), text=tok),
                                        reason="Abbreviation not in the MedLens glossary — ask your clinician or the lab what it means.")
    for m in SUFFIX_RE.finditer(text):
        tok = m.group(0)
        if inside(m.start(), m.end()) or tok.upper() in STOPWORDS:
            continue
        key = tok.lower()
        if key not in out:
            out[key] = UnrecognizedTerm(id=f"u{len(out)}", text=tok, span=Span(start=m.start(), end=m.end(), text=tok),
                                        reason="Medical-looking term not in the MedLens glossary — ask your clinician what it means.")
    return list(out.values())


def glossary_entry(term_id: str) -> Optional[dict]:
    return load_glossary().get(term_id)
