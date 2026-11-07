"""Real-world test 2: a real chatbot's answer about synthetic sample 01 (samples/ai/01_cbc__chatbot_real.txt, verbatim).

Precision beats recall for CONTRADICTED: the answer's numbers are right, so nothing may be CONTRADICTED;
its interpretations ('points to mild anemia', 'reacting to a recent mild infection'...) must be OVERSTEPS.
"""
from pathlib import Path

import pytest

from app.verify.check import verify

ROOT = Path(__file__).resolve().parent.parent
AI = (ROOT / "samples" / "ai" / "01_cbc__chatbot_real.txt").read_text()
R = verify((ROOT / "samples" / "01_cbc.txt").read_text(), AI)


def sentence_with(fragment):
    hits = [s for s in R.sentences if fragment in s.text]
    assert len(hits) == 1, (fragment, [s.text for s in hits])
    return hits[0]


@pytest.mark.parametrize("fragment", [
    "two main patterns: mild anemia and signs of a mild immune response",
    "points to **mild anemia**",
    "is often an early sign of a nutritional deficiency",
    "usually indicates that the body is reacting to a recent mild infection",
    "indicates normal blood clotting ability",
    "commonly occur together during recovery from a minor illness",
])
def test_interpretations_are_oversteps(fragment):
    s = sentence_with(fragment)
    assert s.label == "OVERSTEPS"
    assert all(f.label != "CONTRADICTED" for f in s.findings)


def test_mcv_bullet_is_verified_not_contradicted():
    s = sentence_with("Normal MCV (89.0 fL)")
    assert s.label == "VERIFIED"
    assert [f.term for f in s.findings if f.kind == "status"] == ["Mean Corpuscular Volume"]


def test_platelet_flag_sentence_verified_as_a_correctly_noted_mismatch():
    s = sentence_with('has an "H" (High) flag next to the platelet count')
    assert s.label == "VERIFIED"
    flag = [f for f in s.findings if f.kind == "flag"]
    assert len(flag) == 1 and flag[0].message == "The AI correctly noted the lab's flag doesn't match the printed range."


def test_low_hemoglobin_rbc_pcv_lead_verified():
    s = sentence_with("Low Hemoglobin (11.2 g/dL), Total RBC (4.10), and PCV (36.5%)")
    st = {f.term: f.label for f in s.findings if f.kind == "status"}
    assert s.label == "VERIFIED" and len(st) == 3 and set(st.values()) == {"VERIFIED"}


def test_normal_differentials_group_claim_verified():
    s = sentence_with("Normal Differentials")
    g = [f for f in s.findings if f.kind == "group"]
    assert s.label == "VERIFIED" and len(g) == 1 and g[0].label == "VERIFIED"
    assert "ESR" not in g[0].message                    # ESR shares the section but is not a differential


@pytest.mark.parametrize("fragment", ["is a general marker for inflammation", "A doctor would review these numbers"])
def test_not_oversteps(fragment):
    s = sentence_with(fragment)
    assert all(f.kind != "overstep" for f in s.findings)


def test_doctor_deferral_sentence_is_neutral():
    assert sentence_with("A doctor would review these numbers").label == "NEUTRAL"


def test_only_mchc_is_omitted():
    assert [o.test_name for o in R.omissions] == ["MCHC"]


def test_zero_contradicted():
    assert R.counts["CONTRADICTED"] == 0


def test_six_oversteps():
    assert R.counts["OVERSTEPS"] == 6
