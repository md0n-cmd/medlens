import pytest

from app.classify import classify_line, find_hedges
from app.glossary.matcher import load_glossary
from app.models import Tag
from app.safety import SAFE_FALLBACK, is_safe, out_of_range_message, sanitize, violations


@pytest.mark.parametrize("text", [
    "Findings suggestive of cholelithiasis.", "Appearance consistent with fatty liver.",
    "Malignancy cannot be ruled out.", "Likely a simple cyst.", "Probable early changes.",
    "Kindly correlate clinically.", "?Early cholecystitis", "Clinical correlation advised.",
])
def test_hedged_language_needs_professional(text):
    assert find_hedges(text)
    assert classify_line(text, interpretive_section=False) == Tag.PROFESSIONAL


def test_impression_section_needs_professional_even_without_hedges():
    assert classify_line("Hepatomegaly with grade I fatty liver.", interpretive_section=True) == Tag.PROFESSIONAL


def test_plain_findings_not_flagged():
    assert classify_line("Liver is normal in size.", interpretive_section=False) is None
    assert find_hedges("Portal vein is normal in calibre.") == []


@pytest.mark.parametrize("text", [
    "You have diabetes.", "You are suffering from anaemia.", "The diagnosis is hypothyroidism.",
    "You should take iron tablets.", "Increase the dose of your medicine.", "This can cure the problem.",
    "Treat with antibiotics.", "This means you have liver disease.", "Take 500 mg twice daily.",
    "You are diabetic.", "Nothing to worry about.", "You need insulin.", "Start metformin.",
])
def test_safety_blocks_diagnostic_and_treatment_phrasing(text):
    assert violations(text), text
    assert sanitize(text) == (SAFE_FALLBACK, True)


def test_safety_allows_neutral_text():
    ok = "Measures the amount of hemoglobin in your blood."
    assert is_safe(ok) and sanitize(ok) == (ok, False)


def test_out_of_range_template_is_safe_and_exact():
    msg = out_of_range_message("above", "13–17")
    assert msg.startswith("This value is above the reference range printed on your report (13–17).")
    assert "discuss it with your doctor" in msg and is_safe(msg)
    assert is_safe(out_of_range_message("below", "< 200"))


def test_every_glossary_entry_passes_safety_filter():
    for e in load_glossary().values():
        for field in ("plain_explanation", "what_it_is_not"):
            if e.get(field):
                assert is_safe(e[field]), (e["id"], violations(e[field]))
