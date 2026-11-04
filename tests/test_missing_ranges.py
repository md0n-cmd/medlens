from pathlib import Path

from app.models import Status
from app.pipeline import NO_RANGE_TEXT, analyze

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "07_missing_ranges.txt"


def rows():
    return analyze(SAMPLE.read_text()).values


def test_every_missing_reason_is_covered():
    reasons = {v.test_name + ":" + v.value_raw: v.missing_range_reason for v in rows()}
    assert reasons["Vitamin B12:450"] == "no_range_printed"
    for name in ("Serum Ferritin:38", "Serum Iron:72", "CRP:4.2", "Calcium:9.6", "Uric Acid:5.1"):
        assert reasons[name] == "placeholder", name
    assert reasons["Phosphorus:3.8"] == "range_unreadable"
    assert reasons["Sodium:139"] == "unit_mismatch"
    assert reasons["HBsAg:Non-reactive"] == "qualitative_no_reference"


def test_missing_ranges_are_never_flagged_and_use_fixed_wording():
    for v in rows():
        if v.missing_range_reason:
            assert v.status not in (Status.ABOVE, Status.BELOW, Status.DIFFERS)
            assert NO_RANGE_TEXT in v.status_explanation


def test_range_from_another_row_is_not_borrowed():
    fbs = [v for v in rows() if v.test_name == "Fasting Blood Sugar"]
    assert fbs[0].status == Status.ABOVE
    assert fbs[1].status == Status.NO_RANGE and fbs[1].missing_range_reason == "other_row_only"
    assert "range printed for another row of this test; not applied" in fbs[1].notes


def test_summary_lists_missing_with_reasons():
    s = analyze(SAMPLE.read_text()).structured_summary
    assert len(s.missing_range) == 10
    assert {m.reason for m in s.missing_range} == {"no_range_printed", "placeholder", "range_unreadable",
                                                   "unit_mismatch", "qualitative_no_reference", "other_row_only"}


def test_nil_is_still_a_valid_qualitative_reference():
    r = analyze("Casts    Absent    Nil")
    assert r.values[0].status == Status.WITHIN
