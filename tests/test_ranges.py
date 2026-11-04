import pytest

from app.models import Status
from app.parser.ranges import (compare_numeric, compare_qualitative, parse_range,
                               select_alternative, units_compatible)


@pytest.mark.parametrize("text,lo,hi", [
    ("70-110", 70, 110),
    ("70 - 110", 70, 110),
    ("70–110", 70, 110),          # en dash
    ("70 — 110", 70, 110),        # em dash
    ("70 to 110", 70, 110),
    ("(13.0-17.0)", 13.0, 17.0),  # brackets
    ("[4.0 - 11.0]", 4.0, 11.0),
    ("1,50,000 - 4,10,000", 150000, 410000),   # Indian grouping
    ("150,000 - 410,000", 150000, 410000),     # Western grouping
    ("0.27 - 4.20", 0.27, 4.20),
    ("Ref: 13-17", 13, 17),
])
def test_interval_formats(text, lo, hi):
    r = parse_range(text)
    assert r.kind == "interval"
    assert r.low == lo and r.high == hi


def test_interval_with_scientific_unit():
    r = parse_range("4.0 - 11.0 x10^3/uL")
    assert r.kind == "interval" and r.unit == "x10^3/uL"
    r = parse_range("150 - 410 10^9/L")
    assert r.kind == "interval" and r.unit == "10^9/L"


@pytest.mark.parametrize("text,high,inclusive", [
    ("<200", 200, False), ("< 200", 200, False), ("≤ 5", 5, True), ("<=5", 5, True),
    ("Up to 5.0", 5.0, True), ("Upto 40", 40, True), ("Less than 5", 5, False),
    ("Up to 40 U/L", 40, True),
])
def test_upper_bound_formats(text, high, inclusive):
    r = parse_range(text)
    assert r.kind == "upper" and r.high == high and r.high_inclusive is inclusive


@pytest.mark.parametrize("text,low,inclusive", [
    (">40", 40, False), ("> 40", 40, False), ("≥ 60", 60, True), (">=60", 60, True),
    ("More than 40", 40, False), ("60 and above", 60, True),
])
def test_lower_bound_formats(text, low, inclusive):
    r = parse_range(text)
    assert r.kind == "lower" and r.low == low and r.low_inclusive is inclusive


def test_qualitative_range():
    r = parse_range("Negative")
    assert r.kind == "qualitative" and r.qualitative == "Negative"


@pytest.mark.parametrize("text", ["M: 13-17 F: 12-15", "Male: 13.0-17.0; Female: 12.0-15.0",
                                  "M: 0.7-1.3   F: 0.6-1.1", "Men 13-17 / Women 12-15"])
def test_sex_specific_ranges_are_multi(text):
    r = parse_range(text)
    assert r.kind == "multi" and len(r.alternatives) == 2


@pytest.mark.parametrize("text", ["see note", "", "abc - def", "17 - 13", "Adult: see chart"])
def test_unparseable(text):
    assert parse_range(text).kind == "unparseable"


# ------------------------------------------------------------- comparison
def rng(t):
    return parse_range(t)


def test_inclusive_boundaries_are_within():
    assert compare_numeric(13.0, None, rng("13.0-17.0")) == Status.WITHIN
    assert compare_numeric(17.0, None, rng("13.0-17.0")) == Status.WITHIN
    assert compare_numeric(40, None, rng("Up to 40")) == Status.WITHIN


def test_exclusive_upper_boundary_is_out():
    assert compare_numeric(200, None, rng("<200")) == Status.ABOVE
    assert compare_numeric(199.9, None, rng("<200")) == Status.WITHIN


def test_exclusive_lower_boundary_is_out():
    assert compare_numeric(40, None, rng(">40")) == Status.BELOW
    assert compare_numeric(60, None, rng("≥ 60")) == Status.WITHIN


def test_above_and_below():
    assert compare_numeric(18.2, None, rng("13-17")) == Status.ABOVE
    assert compare_numeric(11.2, None, rng("13-17")) == Status.BELOW


def test_censored_values():
    # <0.01 is entirely below 0.27
    assert compare_numeric(0.01, "<", rng("0.27 - 4.20")) == Status.BELOW
    # <5 vs 0.27-4.2: could be inside or below -> indeterminate
    assert compare_numeric(5, "<", rng("0.27 - 4.20")) == Status.INDETERMINATE
    # >1000 vs <200 -> above
    assert compare_numeric(1000, ">", rng("<200")) == Status.ABOVE
    # <0.5 vs upper bound <1 -> within
    assert compare_numeric(0.5, "<", rng("< 1")) == Status.WITHIN
    # >=200 vs <200 -> above (200 itself is out)
    assert compare_numeric(200, ">=", rng("<200")) == Status.ABOVE


def test_qualitative_comparison():
    assert compare_qualitative("Negative", rng("Negative")) == Status.WITHIN
    assert compare_qualitative("Non-reactive", rng("Non reactive")) == Status.WITHIN
    assert compare_qualitative("Positive", rng("Negative")) == Status.DIFFERS
    assert compare_qualitative("Trace", rng("Negative")) == Status.DIFFERS
    assert compare_qualitative("Pale yellow", rng("Clear")) == Status.DIFFERS


def test_unit_compatibility():
    assert units_compatible("mg/dL", "mg/dl")
    assert units_compatible("µIU/mL", "uIU/ml")
    assert units_compatible("mg/dL", None)          # range without unit -> fine
    assert not units_compatible("mmol/L", "mg/dL")


def test_select_alternative_requires_header_sex():
    r = rng("M: 13-17 F: 12-15")
    chosen, note = select_alternative(r, None)
    assert chosen is None and "ask your clinician" in note
    chosen, note = select_alternative(r, "female")
    assert chosen.low == 12 and "Female" in note
