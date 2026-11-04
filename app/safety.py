"""Guardrail applied to every piece of text MedLens generates (not to the report itself).

MedLens may describe what a test measures and what the report literally says.
It must never diagnose, prognose or recommend treatment.
"""
from __future__ import annotations

import re

BLOCKED_PATTERNS = [
    (r"\byou\s+(?:have|may\s+have|might\s+have|probably\s+have|likely\s+have|are\s+suffering|suffer)\b", "diagnostic claim about the user"),
    (r"\byou(?:'re|\s+are)\s+(?:diabetic|an(?:a)?emic|hypothyroid|hyperthyroid|obese|pre-?diabetic|sick|ill|healthy|fine|normal)\b", "diagnostic label for the user"),
    (r"\b(?:the\s+|your\s+)?diagnosis\s+(?:is|was|of)\b", "states a diagnosis"),
    (r"\bdiagnosed\s+with\b", "states a diagnosis"),
    (r"\bthis\s+(?:means|indicates|confirms|proves|shows)\s+(?:that\s+)?you\s+(?:have|are)\b", "interprets result for the user"),
    (r"\byou\s+(?:should|must|need\s+to|have\s+to)\s+(?:take|start|stop|use|increase|decrease|avoid|begin)\b", "treatment instruction"),
    (r"\byou\s+need\s+(?:medication|medicine|treatment|surgery|insulin|antibiotics?)\b", "treatment instruction"),
    (r"\b(?:dose|dosage|doses)\b", "dosing"),
    (r"\bcur(?:e|es|ed|ing)\b", "cure claim"),
    (r"\btreat(?:ed|ing)?\s+(?:with|by|using)\b", "treatment recommendation"),
    (r"\b(?:prescribe|prescription|prescribed)\b", "prescription"),
    (r"\b\d+\s*(?:mg|mcg|µg|ug|iu|units?|tablets?|tabs?|capsules?)\s+(?:once|twice|daily|a\s+day|per\s+day|bd|od|tds)\b", "drug regimen"),
    (r"\btake\s+(?:a\s+)?(?:tablet|pill|capsule|medicine|medication|supplement|\d+)", "drug recommendation"),
    (r"\b(?:metformin|insulin\s+therapy|statins?|atorvastatin|rosuvastatin|levothyroxine|thyroxine\s+tablets?|antibiotics?\s+course|paracetamol|ibuprofen|aspirin)\b", "names a drug in a recommending context"),
    (r"\bnothing\s+to\s+worry\b|\bno\s+need\s+to\s+(?:worry|see\s+a\s+doctor)\b", "false reassurance"),
    (r"\byou\s+(?:are|will\s+be)\s+(?:at\s+risk|going\s+to)\b", "prognosis"),
]
_COMPILED = [(re.compile(p, re.I), why) for p, why in BLOCKED_PATTERNS]

SAFE_FALLBACK = "[Explanation withheld by the MedLens safety filter. Please ask your clinician about this item.]"

DISCLAIMER = ("MedLens provides informational assistance only. It is NOT a diagnosis, medical opinion or treatment "
              "advice. Reference ranges come only from your report. Always discuss your results with a qualified "
              "clinician.")


def violations(text: str) -> list[str]:
    return [why for rx, why in _COMPILED if rx.search(text or "")]


def is_safe(text: str) -> bool:
    return not violations(text)


def sanitize(text: str) -> tuple[str, bool]:
    """Return (safe_text, was_filtered)."""
    if is_safe(text):
        return text, False
    return SAFE_FALLBACK, True


def out_of_range_message(direction: str, range_display: str) -> str:
    """The ONLY wording MedLens uses for an out-of-range numeric value."""
    assert direction in ("above", "below")
    return (f"This value is {direction} the reference range printed on your report ({range_display}). "
            "What this means for you depends on your overall health and history — discuss it with your doctor.")
