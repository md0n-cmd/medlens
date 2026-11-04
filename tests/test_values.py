from app.parser.lines import segment, split_lines
from app.parser.values import parse_line


def row(t):
    return parse_line(split_lines(t)[0])


def test_columnar_row_with_flag_column():
    r = row("Hemoglobin      11.2   L    g/dL     13.0 - 17.0")
    assert (r.test_name, r.value_num, r.flag_raw, r.unit, r.range_raw) == ("Hemoglobin", 11.2, "L", "g/dL", "13.0 - 17.0")


def test_spans_point_into_original_text():
    t = "Hemoglobin      11.2   L    g/dL     13.0 - 17.0"
    r = row(t)
    for sp in (r.test_span, r.value_span, r.flag_span, r.unit_span, r.range_span):
        assert t[sp.start:sp.end] == sp.text


def test_inline_formats():
    r = row("Hemoglobin: 11.2 g/dL (13.0-17.0)")
    assert (r.value_num, r.unit, r.range_raw) == (11.2, "g/dL", "13.0-17.0")
    r = row("Hemoglobin 11.2 g/dL Ref: 13.0-17.0")
    assert (r.value_num, r.range_raw) == (11.2, "13.0-17.0")


def test_thousand_separator_value():
    assert row("Platelet Count     1,50,000    /cumm    1,50,000 - 4,10,000").value_num == 150000


def test_censored_value():
    r = row("TSH    <0.01   µIU/mL   0.27 - 4.20")
    assert r.value_comparator == "<" and r.value_num == 0.01


def test_attached_and_star_flags():
    assert row("MCHC  33.1*  g/dL  (32.0-36.0)").flag_raw == "*"
    assert row("Total Cholesterol   215 H  mg/dL  <200").flag_raw == "H"


def test_qualitative_value():
    r = row("HBsAg    Non-reactive     Non-reactive")
    assert r.value_qualitative == "Non-reactive" and r.range_raw == "Non-reactive"


def test_value_without_range():
    r = row("Vitamin D   18  ng/mL")
    assert r.value_num == 18 and r.range_raw is None


def test_non_result_lines_ignored():
    for t in ["Age: 45 Years   Sex: Male", "Right kidney measures 10.2 x 4.5 cm",
              "Impression: 1. Fatty liver grade 1", "Test Name   Result   Unit   Reference Range", ""]:
        assert row(t) is None, t


def test_sections_and_interpretive_flag():
    text = "FINDINGS:\nLiver is enlarged.\nIMPRESSION:\nMild hepatomegaly."
    lines, sections = segment(text)
    assert [s.name for s in sections] == ["FINDINGS", "IMPRESSION"]
    assert sections[1].interpretive and not sections[0].interpretive
    assert lines[3].interpretive
