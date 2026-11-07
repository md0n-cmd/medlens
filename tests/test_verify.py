"""MedLens Verify: checking AI-written explanations against the report (rule-based)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.safety import is_safe
from app.verify.check import verify

ROOT = Path(__file__).resolve().parent.parent
SAMPLES, AI = ROOT / "samples", ROOT / "samples" / "ai"


def run(name):
    report = name.split("__")[0]
    return verify((SAMPLES / f"{report}.txt").read_text(), (AI / f"{name}.txt").read_text())


def sentence(res, starts):
    return next(s for s in res.sentences if s.text.startswith(starts))


def labels(s, kind=None):
    return [f.label for f in s.findings if f.kind != "mention" and (kind is None or f.kind == kind)]


# ---------------------------------------------------------------- planted errors
PLANTED = [
    # (sample, sentence start, finding kind, expected label)
    ("01_cbc__errors", "Your hemoglobin is 12 g/dL", "value", "CONTRADICTED"),              # wrong value
    ("01_cbc__errors", "The normal range for hemoglobin is 12–16", "range", "RANGE_NOT_FROM_REPORT"),  # textbook range
    ("01_cbc__errors", "Your WBC count of 11,800 is normal", "status", "CONTRADICTED"),     # wrong status
    ("01_cbc__errors", "Overall, you have anemia", "overstep", "OVERSTEPS"),                # diagnosis
    ("01_cbc__errors", "You should take iron tablets", "overstep", "OVERSTEPS"),            # dosage advice
    ("01_cbc__errors", "There is nothing to worry about", "overstep", "OVERSTEPS"),         # false reassurance
    ("03_lft_kft__errors", "Your SGOT is 48 U/L", "value", "CONTRADICTED"),
    ("03_lft_kft__errors", "The normal range for ALT is 7–56", "range", "RANGE_NOT_FROM_REPORT"),
    ("03_lft_kft__errors", "This suggests liver disease", "overstep", "OVERSTEPS"),
    ("03_lft_kft__errors", "No need to see a doctor", "overstep", "OVERSTEPS"),
    ("06_age_bands__errors", "The normal TSH range is 0.5–5.0", "range", "RANGE_NOT_FROM_REPORT"),
    ("06_age_bands__errors", "Your total cholesterol is 159", "value", "CONTRADICTED"),
    ("06_age_bands__errors", "This confirms hypothyroidism", "overstep", "OVERSTEPS"),
    ("06_age_bands__errors", "Take levothyroxine 50 mcg", "overstep", "OVERSTEPS"),
    ("07_missing_ranges__errors", "Your vitamin B12 is 450", "range", "RANGE_NOT_FROM_REPORT"),  # report prints no range
    ("07_missing_ranges__errors", "Your sodium is 145", "value", "CONTRADICTED"),
    ("07_missing_ranges__errors", "Your CRP of 4.2 mg/L is elevated", "overstep", "OVERSTEPS"),
    ("07_missing_ranges__errors", "You don't need to worry", "overstep", "OVERSTEPS"),
]


@pytest.mark.parametrize("sample,start,kind,label", PLANTED)
def test_planted_error_is_caught(sample, start, kind, label):
    s = sentence(run(sample), start)
    assert label in labels(s, kind), (s.text, [(f.kind, f.label) for f in s.findings])


def test_sentence_label_is_most_severe_finding():
    s = sentence(run("01_cbc__errors"), "Your hemoglobin is 12 g/dL")
    assert labels(s, "status") == ["VERIFIED"] and s.label == "CONTRADICTED"


def test_omitted_out_of_range_values_are_reported():
    om = {o.test_name for o in run("01_cbc__errors").omissions}
    assert {"ESR", "MCHC", "RDW-CV", "Total RBC Count", "Packed Cell Volume (PCV)"} == om
    assert "Platelet Count" not in om                                   # within range: not an omission
    om3 = {o.test_name for o in run("03_lft_kft__errors").omissions}
    assert om3 == {"Bilirubin, Direct", "Bilirubin, Indirect"}


def test_correct_explanation_has_no_problems():
    r = run("01_cbc__correct")
    bad = [f for s in r.sentences for f in s.findings if f.label in ("CONTRADICTED", "RANGE_NOT_FROM_REPORT", "OVERSTEPS")]
    assert bad == [] and r.omissions == []
    assert r.counts["VERIFIED"] >= 20


# ---------------------------------------------------------------- matching details
REPORT = (SAMPLES / "01_cbc.txt").read_text()


def one(ai, report=REPORT):
    r = verify(report, ai)
    return r, r.sentences[0]


@pytest.mark.parametrize("ai", ["Your platelet count is 2,10,000.", "Your platelet count is 210000.",
                                "Your platelets are 2.1 lakh.", "Your platelet count is 210,000."])
def test_number_formats_match(ai):
    _, s = one(ai)
    assert labels(s, "value") == ["VERIFIED"]


def test_aliases_hb_and_sgpt():
    _, s = one("Hb of 11.2 is low.")
    assert labels(s) == ["VERIFIED", "VERIFIED"]
    _, s = one("Your SGPT is 62.", (SAMPLES / "03_lft_kft.txt").read_text())
    assert labels(s, "value") == ["VERIFIED"]
    _, s = one("Your ALT is 62.", (SAMPLES / "03_lft_kft.txt").read_text())       # same test, other name
    assert labels(s, "value") == ["VERIFIED"]


def test_two_claims_in_one_sentence():
    _, s = one("Your MCV is 89 fL and your MCHC is 33.")
    vals = [f for f in s.findings if f.kind == "value"]
    assert [f.label for f in vals] == ["VERIFIED", "CONTRADICTED"] and s.label == "CONTRADICTED"


def test_status_on_ambiguous_range_is_unverifiable():
    _, s = one("Your creatinine is normal.", (SAMPLES / "03_lft_kft.txt").read_text())
    assert labels(s) == ["UNVERIFIABLE"] and s.label == "UNVERIFIABLE"


def test_test_not_in_report():
    _, s = one("Your vitamin D is 30 ng/mL.")
    assert labels(s) == ["NOT_IN_REPORT"]


def test_paraphrase_without_test_name_is_neutral():
    """Known limit: paraphrases are not extracted, so they come out NEUTRAL (not verified)."""
    _, s = one("Your blood count is a bit off.")
    assert s.label == "NEUTRAL"


def test_negated_status_is_not_a_claim():
    _, s = one("Your ESR is not normal.")
    assert labels(s, "status") == []


def test_dose_number_is_not_a_test_value():
    _, s = one("Take iron tablets 100 mg twice a day.")
    assert labels(s, "value") == [] and labels(s) == ["OVERSTEPS"]


def test_value_written_as_span():
    report = (SAMPLES / "04_thyroid_urine.txt").read_text()
    _, s = one("Your pus cells are 6-8 /hpf.", report)
    assert labels(s, "value") == ["VERIFIED"]


def test_status_judged_against_the_row_whose_value_matched():
    report = (SAMPLES / "07_missing_ranges.txt").read_text()       # FBS printed twice: 104 (range) and 98 (no range)
    _, s = one("Your fasting blood sugar is 104 mg/dL, which is high.", report)
    assert labels(s) == ["VERIFIED", "VERIFIED"]
    _, s = one("Your fasting blood sugar is 98 mg/dL, which is normal.", report)
    assert labels(s) == ["VERIFIED", "UNVERIFIABLE"]


# ---------------------------------------------------------------- spans + safety
@pytest.mark.parametrize("name", sorted(p.stem for p in AI.glob("*.txt")))
def test_spans_valid_in_both_texts(name):
    ai = (AI / f"{name}.txt").read_text()
    r = run(name)
    rtext = r.report_analysis.text
    for s in r.sentences:
        assert ai[s.span.start:s.span.end] == s.text
        for f in s.findings:
            assert ai[f.ai_span.start:f.ai_span.end] == f.ai_span.text
            assert s.span.start <= f.ai_span.start and f.ai_span.end <= s.span.end
            if f.report_span:
                assert rtext[f.report_span.start:f.report_span.end] == f.report_span.text
    for o in r.omissions:
        assert rtext[o.report_span.start:o.report_span.end] == o.report_span.text


@pytest.mark.parametrize("name", sorted(p.stem for p in AI.glob("*.txt")))
def test_medlens_own_messages_pass_safety(name):
    r = run(name)
    for s in r.sentences:
        for f in s.findings:
            assert is_safe(f.message), f.message
    for o in r.omissions:
        assert is_safe(o.message)


# ---------------------------------------------------------------- API
client = TestClient(app)


def test_api_verify():
    r = client.post("/api/verify", json={"report_text": REPORT, "ai_text": "Your hemoglobin is 11.2 g/dL."})
    body = r.json()
    assert r.status_code == 200 and body["sentences"][0]["label"] == "VERIFIED"
    assert body["report_analysis"]["values"] and body["note"].startswith("MedLens checks what the AI said")


@pytest.mark.parametrize("payload", [{"report_text": "", "ai_text": "x"}, {"report_text": REPORT, "ai_text": "   "}])
def test_api_verify_empty_inputs_400(payload):
    assert client.post("/api/verify", json=payload).status_code == 400


def test_api_verify_size_limit():
    assert client.post("/api/verify", json={"report_text": REPORT, "ai_text": "x" * 100_001}).status_code == 422


def test_api_verify_is_rate_limited():
    from app.ratelimit import LIMITED_PATHS
    assert "/api/verify" in LIMITED_PATHS


def test_api_ai_samples_listed():
    s = client.get("/api/ai-samples").json()
    assert len(s) == 5 and sum(x["kind"] == "correct" for x in s) == 1
    assert all("SYNTHETIC AI EXPLANATION" in x["text"] for x in s)


def test_range_in_lakh_matches_report_range():
    _, s = one("Platelets should be 1.5–4.1 lakh.")
    assert labels(s) == ["VERIFIED"]


def test_different_unit_is_unverifiable_not_contradicted():
    report = (SAMPLES / "07_missing_ranges.txt").read_text()
    _, s = one("Your fasting glucose is 5.8 mmol/L.", report)
    assert labels(s) == ["UNVERIFIABLE"] and "does not convert units" in s.findings[0].message


def test_known_limits_are_neutral_not_verified():
    """Documented limits: these are shown but not judged."""
    assert one("12 g/dL of hemoglobin was found.")[1].label == "NEUTRAL"          # number before the test name
    assert one("Your HBsAg is negative.", (SAMPLES / "04_thyroid_urine.txt").read_text())[1].label == "NEUTRAL"  # qualitative
    r = verify(REPORT, "Your hemoglobin is low. It is 11.2.")                   # pronoun across sentences
    assert r.sentences[1].label == "NEUTRAL"


# ---------------------------------------------------------------- real-world fixes: test-name matching
@pytest.mark.parametrize("report_name,ai_name", [
    ("Cholesterol, Total", "Total Cholesterol"), ("GLUCOSE FASTING (F), PLASMA", "Fasting Glucose"),
    ("VLDL Cholesterol,Calculated", "VLDL Cholesterol"), ("T3, Total", "Total T3"), ("Bilirubin Total", "Total Bilirubin"),
    ("E.S.R", "ESR"), ("Packed Cell Volume (PCV)", "PCV"),
])
def test_report_and_ai_test_names_normalise_to_the_same_test(report_name, ai_name):
    from app.verify.names import names_match
    assert names_match(report_name, ai_name)


def test_different_tests_do_not_normalise_together():
    from app.verify.names import names_match
    assert not names_match("Bilirubin Direct", "Total Bilirubin")
    assert not names_match("HDL Cholesterol", "LDL Cholesterol")


# ---------------------------------------------------------------- real-world fixes: sentence splitting
@pytest.mark.parametrize("text,expected", [
    ("* E.S.R. (Erythrocyte Sedimentation Rate): 19 mm/hr (Reference: 0 - 15 mm/hr), which is slightly elevated.", 1),
    ("See Dr. Rao and Mrs. Iyer. Your Hb is 11.2 g/dL. It is low.", 3),
    ("Some tests (e.g. TSH. T3) are fine. Grade A. Next one.", 2),
    ("Values i.e. the numbers are listed. No. 5 is here.", 2),
    ("Your eGFR is 78. Drink water! Really? Yes.", 4),
])
def test_sentence_splitting_respects_abbreviations_decimals_parentheses(text, expected):
    from app.verify.claims import split_sentences
    assert len(split_sentences(text)) == expected


def test_dotted_esr_stays_one_sentence_and_matches_report():
    report = "HAEMATOLOGY\nE.S.R      19      mm/hr      0 - 15\n"
    r = verify(report, "E.S.R. (Erythrocyte Sedimentation Rate): 19 mm/hr (Reference: 0 - 15 mm/hr), which is slightly elevated.")
    assert len(r.sentences) == 1
    s = r.sentences[0]
    assert s.label == "VERIFIED" and set(labels(s)) == {"VERIFIED"} and len(labels(s)) == 3


# ---------------------------------------------------------------- real-world fixes: oversteps
LIPID = "LIPID SCREEN, SERUM\nCholesterol, Total   212.00   mg/dL   < 200.00\nTriglycerides   168.00   mg/dL   < 150.00\n"


@pytest.mark.parametrize("ai,phrase", [
    ("These results indicate dyslipidemia.", "indicate dyslipidemia"),
    ("Your lipid profile indicates dyslipidemia.", "indicates dyslipidemia"),
    ("Findings point to hepatomegaly.", "point to hepatomegaly"),
    ("The pattern is consistent with fatty liver.", "consistent with fatty liver"),
])
def test_finding_words_after_indicate_suggest_are_oversteps(ai, phrase):
    r = verify(LIPID, ai)
    s = r.sentences[0]
    assert s.label == "OVERSTEPS" and "NOT_IN_REPORT" not in labels(s)
    assert phrase in s.findings[-1].message and "diagnosis only your doctor can make" in s.findings[-1].message


@pytest.mark.parametrize("ai", ["Your thyroid hormones are fine, indicating normal thyroid function.",
                                "Your liver is healthy.", "This shows healthy liver function.", "You are in good health."])
def test_health_interpretations_are_oversteps(ai):
    s = verify(LIPID, ai).sentences[0]
    assert s.label == "OVERSTEPS" and "statement about your health" in s.findings[-1].message


def test_quoting_the_report_is_not_an_overstep():
    s = verify(LIPID, "The report's impression says findings suggestive of cholelithiasis.").sentences[0]
    assert s.label == "NEUTRAL"


def test_overstep_messages_never_repeat_unsafe_phrases():
    r = verify(LIPID, "Overall, you have anemia. Take 100 mg twice a day.")
    for s in r.sentences:
        assert s.label == "OVERSTEPS" and all(is_safe(f.message) for f in s.findings)
