"""Regression tests from a real-world run of MedLens Verify (a real lab report + a real chatbot explanation).

The real texts are NOT in this repo. samples/10_lipid_thyroid_lft_cbc.txt and samples/ai/10__realistic.txt are
synthetic replicas: fictional patient, doctor and lab, changed values, same layout and same chatbot style.
"""
from pathlib import Path

from app.models import Status
from app.pipeline import analyze
from app.verify.check import verify

ROOT = Path(__file__).resolve().parent.parent
REPORT = (ROOT / "samples" / "10_lipid_thyroid_lft_cbc.txt").read_text()
AI = (ROOT / "samples" / "ai" / "10__realistic.txt").read_text()
R = verify(REPORT, AI)


def sentence(starts):
    return next(s for s in R.sentences if s.text.startswith(starts))


def finding(s, kind):
    return next(f for f in s.findings if f.kind == kind)


def test_lft_group_claim_contradicted_naming_total_protein():
    s = sentence("Liver enzymes and proteins are all within normal ranges")
    f = finding(s, "group")
    assert s.label == "CONTRADICTED" and f.label == "CONTRADICTED"
    assert f.message == ("The AI says these are all within range, but Total Protein (6.33) is below the range "
                         "printed on your report (6.40–8.30).")


def test_cbc_group_claim_contradicted_naming_rdw():
    f = finding(sentence("Your blood counts are healthy"), "group")
    assert f.label == "CONTRADICTED" and "RDW-CV" in f.message and "(14.3) is above" in f.message
    assert "E.S.R" not in f.message                                  # ESR is its own test, not a blood count


def test_dyslipidemia_is_an_overstep_not_missing_from_report():
    s = sentence("These results indicate dyslipidemia")
    assert s.label == "OVERSTEPS" and [f.label for f in s.findings if f.kind != "mention"] == ["OVERSTEPS"]


def test_normal_thyroid_function_is_an_overstep():
    s = sentence("Your thyroid hormones are within standard reference intervals")
    assert s.label == "OVERSTEPS"
    assert finding(s, "group").label == "VERIFIED"                    # the numbers part is right
    assert "normal thyroid function" in finding(s, "overstep").message


def test_total_cholesterol_verified_and_not_omitted():
    s = sentence("Total Cholesterol: 212 mg/dL")         # bullet marker is markup, not part of the sentence
    assert s.label == "VERIFIED"
    assert "Cholesterol, Total" not in {o.test_name for o in R.omissions}


def test_esr_is_one_sentence_and_verified():
    esr = [s for s in R.sentences if "E.S.R." in s.text]
    assert len(esr) == 1 and esr[0].label == "VERIFIED" and "slightly elevated" in esr[0].text
    assert {f.kind for f in esr[0].findings if f.label == "VERIFIED"} == {"value", "range", "status"}


def test_no_result_rows_from_risk_or_trimester_tables():
    a = analyze(REPORT)
    names = [v.test_name.lower() for v in a.values]
    assert not any(w in n for n in names for w in ("optimal", "borderline", "risk", "trimester", "very high"))
    assert len(a.values) == 41
    assert "1st Trimester" in a.text and "Borderline High" in a.text  # still visible as plain text


def test_zero_not_in_report_findings():
    assert R.counts["NOT_IN_REPORT"] == 0


def test_only_true_omission_is_reported():
    assert [o.test_name for o in R.omissions] == ["Non-HDL Cholesterol"]   # above range, never mentioned


def test_replica_is_synthetic():
    assert "SYNTHETIC SAMPLE" in REPORT and "SYNTHETIC AI EXPLANATION" in AI
    assert "fictional" in REPORT.lower()
    a = analyze(REPORT)
    assert a.patient_sex == "male" and by_status(a, Status.BELOW) == ["Total Protein"]


def by_status(a, st):
    return [v.test_name for v in a.values if v.status == st]
