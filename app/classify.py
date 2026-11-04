"""Factual vs. general-info vs. needs-professional-interpretation rules.

Tags:
  FACTUAL_FROM_REPORT               value, unit, printed range, computed comparison
  GENERAL_INFO                      glossary definition of what a test/term is
  NEEDS_PROFESSIONAL_INTERPRETATION clinical significance: out-of-range meaning,
                                    Impression/Advice/... sections, hedged language,
                                    combinations of abnormal values, unknown terms
"""
from __future__ import annotations

import re

from app.models import Tag

HEDGE_PATTERNS = [
    r"suggestive\s+of", r"suggests?", r"consistent\s+with", r"compatible\s+with", r"cannot\s+be\s+ruled\s+out",
    r"can\s*not\s+be\s+excluded", r"to\s+rule\s+out", r"r/o", r"likely", r"unlikely", r"probabl[ey]", r"possibl[ey]",
    r"may\s+(?:be|represent|indicate)", r"could\s+(?:be|represent)", r"query", r"correlate\s+clinically",
    r"clinical(?:ly)?\s+correlat\w*", r"likely\s+represents?", r"favou?rs?", r"cannot\s+be\s+commented",
    r"differential\s+(?:diagnosis|includes)", r"advised?", r"recommended", r"follow[\s-]*up", r"further\s+evaluation",
    r"s/o",
]
HEDGE_RE = re.compile(
    r"(?<![A-Za-z])(?:" + "|".join(HEDGE_PATTERNS) + r")(?![A-Za-z])"
    r"|\?\s*(?=[A-Za-z])|(?<=[A-Za-z])\s*\?",   # '?cholecystitis' or 'cholecystitis?' = query
    re.I,
)


def find_hedges(text: str) -> list[re.Match]:
    return list(HEDGE_RE.finditer(text))


def classify_line(text: str, interpretive_section: bool) -> Tag | None:
    """Return NEEDS_PROFESSIONAL_INTERPRETATION for report text that carries clinical opinion."""
    if not text.strip():
        return None
    if interpretive_section or find_hedges(text):
        return Tag.PROFESSIONAL
    return None


def tag_for_status_text() -> Tag:
    return Tag.FACTUAL


def tag_for_glossary() -> Tag:
    return Tag.GENERAL
