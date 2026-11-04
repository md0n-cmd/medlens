from pathlib import Path

import pytest

from app.models import Status, Tag
from app.pipeline import analyze
from app.safety import is_safe

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def run(name):
    return analyze((SAMPLES / name).read_text())


def by_name(res, name):
    return next(v for v in res.values if v.test_name == name)


def test_cbc_sample():
    r = run("01_cbc.txt")
    assert r.patient_sex == "male"
    assert by_name(r, "Hemoglobin").status == Status.BELOW
    assert by_name(r, "Total Leukocyte Count (TLC)").value_num == 11800
    plt = by_name(r, "Platelet Count")
    assert plt.status == Status.WITHIN and plt.flag_raw == "H" and plt.flag_disagreement
    assert by_name(r, "ESR").status == Status.ABOVE       # not flagged by lab, but outside printed range
    assert r.summary.flag_disagreements == 1
    assert any(e.title.startswith("Remarks") and e.tag == Tag.PROFESSIONAL for e in r.explanations)


def test_lipid_diabetes_sample_boundaries():
    r = run("02_lipid_diabetes.txt")
    assert by_name(r, "Triglycerides").status == Status.ABOVE          # 150 vs "< 150": exclusive
    assert by_name(r, "VLDL Cholesterol").status == Status.WITHIN      # 30 vs "Up to 30": inclusive
    assert by_name(r, "HDL Cholesterol").status == Status.BELOW        # 38 vs ">40"
    assert by_name(r, "HbA1c").status == Status.ABOVE
    assert by_name(r, "Non-HDL Cholesterol").status == Status.NO_RANGE  # no built-in ranges, ever


def test_lft_kft_sample_ambiguity_and_units():
    r = run("03_lft_kft.txt")
    assert r.patient_sex is None
    for n in ("GGT", "Serum Creatinine", "Uric Acid"):
        v = by_name(r, n)
        assert v.status == Status.AMBIGUOUS and "ask your clinician" in v.status_explanation
    assert by_name(r, "SGPT (ALT)").glossary_term_id == "alanine_aminotransferase"
    assert by_name(r, "SGOT (AST)").status == Status.WITHIN            # 40 vs Upto 40
    assert by_name(r, "Chloride").status == Status.UNIT_MISMATCH


def test_sex_in_header_selects_range():
    text = (SAMPLES / "03_lft_kft.txt").read_text().replace("Age: 45", "Age: 45   Sex: Male")
    r = analyze(text)
    assert by_name(r, "Uric Acid").status == Status.ABOVE              # 7.4 vs Male 3.4-7.0
    assert any("Sex: Male" in n for n in by_name(r, "Uric Acid").notes)


def test_thyroid_urine_sample_qualitative_and_censored():
    r = run("04_thyroid_urine.txt")
    assert by_name(r, "TSH").status == Status.BELOW                    # <0.01 vs 0.27-4.20
    assert by_name(r, "Vitamin D").status == Status.NO_RANGE
    assert by_name(r, "Urine Protein").status == Status.DIFFERS        # Trace vs Negative
    assert by_name(r, "HBsAg").status == Status.WITHIN
    assert by_name(r, "Pus Cells").status == Status.ABOVE              # 6-8 vs 0-5
    assert by_name(r, "Epithelial Cells").status == Status.NO_RANGE


def test_ultrasound_sample_interpretive_sections():
    r = run("05_ultrasound.txt")
    assert r.values == []                       # narrative measurements are not lab values
    prof = [e for e in r.explanations if e.tag == Tag.PROFESSIONAL]
    assert sum(e.title.startswith("IMPRESSION") for e in prof) == 3
    assert any(e.title == "Hedged language" for e in prof)
    assert r.unrecognized == []                                     # every word now in the glossary
    assert {"cholecystitis", "cholelithiasis"} <= {t.term_id for t in r.terms}
    assert any(t.term_id == "hepatomegaly" for t in r.terms)


@pytest.mark.parametrize("name", sorted(p.name for p in SAMPLES.glob("*.txt")))
def test_all_spans_and_text_safe(name):
    r = run(name)
    for v in r.values:
        for sp in (v.test_span, v.value_span, v.unit_span, v.flag_span, v.reference_span):
            if sp:
                assert r.text[sp.start:sp.end] == sp.text
    for t in r.terms:
        assert r.text[t.span.start:t.span.end] == t.matched_text
    for e in r.explanations:
        assert is_safe(e.text), e.text
        assert e.source_snippet


def test_empty_and_garbage_input():
    r = analyze("hello world")
    assert r.values == [] and r.warnings
    r = analyze("Hemoglobin  12  g/dL  see note")          # "see note" is a placeholder, not a range
    assert r.values[0].status == Status.NO_RANGE and r.values[0].missing_range_reason == "placeholder"
    r = analyze("Hemoglobin  12  g/dL  17 - 13")
    assert r.values[0].status == Status.UNPARSEABLE


def test_crlf_input_normalised():
    r = analyze("Hemoglobin  11.2  g/dL  13-17\r\nTSH  2.0  uIU/mL  0.27-4.2\r\n")
    assert [v.status for v in r.values] == [Status.BELOW, Status.WITHIN]
    assert "\r" not in r.text


@pytest.mark.parametrize("name", sorted(p.name for p in SAMPLES.glob("*.txt")))
def test_banner_line_produces_no_glossary_card(name):
    r = run(name)
    banner_end = r.text.index("\n")
    assert not any(t.span.start < banner_end for t in r.terms)


def test_coag_cardiac_sample():
    r = run("08_coag_cardiac.txt")
    assert by_name(r, "Prothrombin Time").status == Status.ABOVE
    assert by_name(r, "D-dimer").status == Status.ABOVE                 # 0.62 vs "< 0.50"
    assert by_name(r, "CPK").status == Status.WITHIN                    # header Male picks M: 39-308
    assert by_name(r, "Control").missing_range_reason == "no_range_printed"
    assert r.unrecognized == []
    assert {"activated_partial_thromboplastin_time", "troponin", "ck_mb", "b_type_natriuretic_peptide"} <= {t.term_id for t in r.terms}


def test_chest_xray_ecg_sample():
    r = run("09_chest_xray_ecg.txt")
    assert r.unrecognized == []
    ids = {t.term_id for t in r.terms}
    assert {"cardiomegaly", "consolidation", "sinus_rhythm", "electrocardiogram", "qrs_duration"} <= ids
    imp = [e for e in r.explanations if e.title.startswith("IMPRESSION")]
    assert imp and all(e.tag == Tag.PROFESSIONAL for e in imp)
