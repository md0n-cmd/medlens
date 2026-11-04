from pathlib import Path

from app.models import Status
from app.parser.ranges import label_band, parse_range, select_alternative
from app.pipeline import analyze, detect_age

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "06_age_bands.txt"


def by_name(res, name):
    return next(v for v in res.values if v.test_name == name)


def test_age_band_cell_parses_as_multi():
    r = parse_range("Adults: 0.4-4.0 | Children 1-5 yrs: 0.7-6.0")
    assert r.kind == "multi" and [a.label for a in r.alternatives] == ["Adults", "Children 1-5 yrs"]
    r = parse_range("1-17 yrs: 0.9-2.3 | 18-60 years: 0.8-2.0 | >60 Y: 0.6-1.8")
    assert r.kind == "multi" and len(r.alternatives) == 3


def test_label_bands():
    assert (label_band("Children 1-5 yrs").age_min, label_band("Children 1-5 yrs").age_max) == (1, 5)
    assert label_band("Adult male").sex == "male" and label_band("Adult male").age_min == 18
    assert label_band(">60 Y").age_min > 60
    assert label_band("Pregnancy 1st trimester").pregnancy


def test_header_age_picks_band():
    r = analyze(SAMPLE.read_text())
    tsh = by_name(r, "TSH")
    assert tsh.status == Status.ABOVE                                    # 5.2 vs Adults 0.4-4.0
    assert any("because the header says Age 34 Y" in n for n in tsh.notes)
    assert by_name(r, "T3, Total").status == Status.WITHIN              # 18-60 years band


def test_missing_sex_is_ambiguous_never_guessed():
    hdl = by_name(analyze(SAMPLE.read_text()), "HDL Cholesterol")
    assert hdl.status == Status.AMBIGUOUS and "ask your clinician" in hdl.status_explanation


def test_missing_age_is_ambiguous():
    text = SAMPLE.read_text().replace("Age : 34 Years", "")
    assert detect_age(text) is None
    assert by_name(analyze(text), "TSH").status == Status.AMBIGUOUS


def test_overlapping_bands_are_ambiguous():
    chol = by_name(analyze(SAMPLE.read_text()), "Total Cholesterol")
    assert chol.status == Status.AMBIGUOUS and "overlapping" in chol.status_explanation


def test_pregnancy_band_on_continuation_line_is_ambiguous():
    ft4 = by_name(analyze(SAMPLE.read_text()), "Free T4")
    assert ft4.status == Status.AMBIGUOUS
    assert "pregnancy" in ft4.status_explanation and "ask your clinician" in ft4.status_explanation
    assert any("continues on the next line" in n for n in ft4.notes)


def test_age_and_sex_band():
    r = parse_range("Adult male: 13-17 | Adult female: 12-15 | Children: 11-14")
    chosen, note = select_alternative(r, "male", 34)
    assert chosen.low == 13 and "Adult male" in note and "Age 34 Y / Sex: Male" in note
    chosen, note = select_alternative(r, None, 34)
    assert chosen is None and "ask your clinician" in note
