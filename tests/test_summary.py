import json
from pathlib import Path

import pytest

from app.models import Status
from app.pipeline import analyze

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
NAMES = sorted(p.name for p in SAMPLES.glob("*.txt"))


@pytest.mark.parametrize("name", NAMES)
def test_summary_matches_extracted_values(name):
    r = analyze((SAMPLES / name).read_text())
    s = r.structured_summary
    flat = [t for sec in s.sections for t in sec.tests]
    assert [(t.name, t.value, t.status) for t in flat] == [(v.test_name, v.value_raw, v.status) for v in r.values]
    assert sum(s.counts.values()) == len(r.values)
    assert len(s.outside_printed_range) == r.summary.out_of_range


@pytest.mark.parametrize("name", NAMES)
def test_summary_never_contains_patient_name(name):
    s = analyze((SAMPLES / name).read_text()).structured_summary
    dumped = s.model_dump_json()
    assert "Test Patient" not in dumped and "Patient Name" not in dumped


@pytest.mark.parametrize("name", NAMES)
def test_summary_passage_spans_slice_original(name):
    r = analyze((SAMPLES / name).read_text())
    s = r.structured_summary
    spans = [t.passage_span for sec in s.sections for t in sec.tests] + \
            [l.passage_span for l in s.needs_professional_interpretation]
    for sp in spans:
        assert r.text[sp.start:sp.end] == sp.text


def test_narrative_report_summary():
    s = analyze((SAMPLES / "05_ultrasound.txt").read_text()).structured_summary
    assert {"FINDINGS", "IMPRESSION"} <= {sec.title for sec in s.sections}
    quoted = [l.text for l in s.needs_professional_interpretation]
    assert "1. Hepatomegaly with grade I fatty liver." in quoted       # verbatim
    assert s.report["sex"] == "Male" and s.report["age"] == "41 Y"


def test_cbc_header_and_directions():
    s = analyze((SAMPLES / "01_cbc.txt").read_text()).structured_summary
    assert s.report == {"lab": "SUNRISE DIAGNOSTICS (fictional lab)", "date": "04/11/2026",
                        "specimen": "EDTA Whole Blood", "age": "34 Y", "sex": "Male"}
    d = {o.name: o.direction for o in s.outside_printed_range}
    assert d["Hemoglobin"] == "lower" and d["ESR"] == "higher"
