"""The plain-words box is built client-side; its fixed phrases must obey the same safety rules."""
import re
from pathlib import Path

from app.safety import is_safe, violations

HTML = (Path(__file__).resolve().parent.parent / "frontend" / "index.html").read_text(encoding="utf-8")


def plain_phrases():
    block = HTML[HTML.index("const PLAIN = {"):HTML.index("};", HTML.index("const PLAIN = {"))]
    return re.findall(r':\s*"([^"]+)"', block)


def test_plain_words_phrases_exist():
    phrases = plain_phrases()
    assert len(phrases) >= 15
    assert "This is a reading aid, not a diagnosis. Only your doctor can say what these results mean for you." in phrases


def test_plain_words_phrases_pass_safety_filter():
    for p in plain_phrases():
        assert is_safe(p), (p, violations(p))


def test_doctor_questions_are_questions():
    for p in plain_phrases():
        if p.endswith("?"):
            assert not re.search(r"\byou should\b", p, re.I)


def test_verify_summary_leads_with_oversteps():
    start = HTML.index("function verifySummary(v)")
    body = HTML[start:HTML.index("function renderVerify", start)]
    assert body.index("only a doctor can make") < body.index("claims\")} checked")
