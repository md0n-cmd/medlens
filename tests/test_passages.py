from pathlib import Path

import pytest

from app.pipeline import analyze

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.mark.parametrize("name", sorted(p.name for p in SAMPLES.glob("*.txt")))
def test_every_explanation_has_valid_passage_containing_its_span(name):
    r = analyze((SAMPLES / name).read_text())
    for e in r.explanations:
        if e.span is None:
            assert e.passage is None            # e.g. the "several values" combination card
            continue
        p = e.passage
        assert p is not None, e.title
        assert r.text[p.start:p.end] == p.text
        assert p.start <= e.span.start and e.span.end <= p.end, e.title


def test_impression_passage_is_whole_section_block():
    r = analyze((SAMPLES / "05_ultrasound.txt").read_text())
    imp = next(e for e in r.explanations if e.title.startswith("IMPRESSION"))
    assert imp.passage.text.startswith("IMPRESSION:") and "Correlate clinically" in imp.passage.text
