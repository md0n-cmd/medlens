"""MedLens Verify: checking AI-written explanations against the report (rule-based)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.safety import is_safe
from app.verify.check import verify

ROOT = Path(__file__).resolve().parent.parent
SAMPLES, AI = ROOT / "samples", ROOT / "samples" / "ai"


def report_for(name):
    prefix = name.split("__")[0]
    exact = SAMPLES / f"{prefix}.txt"
    return exact if exact.exists() else next(SAMPLES.glob(f"{prefix}_*.txt"))


def run(name):
    return verify(report_for(name).read_text(), (AI / f"{name}.txt").read_text())


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
            if f.kind != "passage":                 # a merged passage spans several sentences by design
                assert s.span.start <= f.ai_span.start and f.ai_span.end <= s.span.end
            for sp in ([f.report_span] if f.report_span else []) + f.extra_report_spans:
                assert rtext[sp.start:sp.end] == sp.text
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
    assert len(s) == 10 and sum(x["kind"] == "correct" for x in s) == 1
    real = [x for x in s if x["kind"] == "chatbot_real"]
    assert len(real) == 4 and all(x["title"].startswith("Real chatbot answer") for x in real)
    assert {x["report_id"] for x in s} <= {p.stem for p in SAMPLES.glob("*.txt")}       # every AI sample has its report
    # the real chatbot answer is kept verbatim (it is about synthetic sample 01, so it has no header of ours)
    assert all("SYNTHETIC AI EXPLANATION" in x["text"] for x in s if x["kind"] != "chatbot_real")
    assert all("synthetic" in x["text"].lower() for x in real)


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
    assert phrase in s.findings[-1].message and "That is an interpretation only your doctor can make" in s.findings[-1].message


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


# ---------------------------------------------------------------- real-world fixes: group claims
THYROID_OK = "THYROID PROFILE, TOTAL, SERUM\nT3, Total   1.12   ng/mL   0.80 - 2.00\nTSH   2.85   µIU/mL   0.27 - 4.20\n"
MIXED_PANEL = ("LIVER & KIDNEY PANEL, SERUM\nTotal Protein   7.0   g/dL   6.40 - 8.30\nAlbumin   4.2   g/dL   3.5 - 5.2\n"
               "Creatinine   2.0   mg/dL   0.70 - 1.30\n")


def group_finding(r, i=0):
    return next(f for f in r.sentences[i].findings if f.kind == "group")


def test_group_claim_contradicted_lists_every_exception():
    r = verify(REPORT, "Your blood counts are all within normal ranges.")
    f = group_finding(r)
    assert f.label == "CONTRADICTED" and r.sentences[0].label == "CONTRADICTED"
    for name in ("Hemoglobin (11.2) is below", "MCHC (30.7) is below", "Total Leukocyte Count (TLC) (11,800) is above"):
        assert name in f.message, f.message
    assert len(f.extra_report_spans) >= 6


def test_group_claim_verified_when_every_member_is_in_range():
    f = group_finding(verify(THYROID_OK, "Your thyroid tests are within standard reference intervals."))
    assert f.label == "VERIFIED" and "all 2 thyroid tests" in f.message


def test_group_claim_unverifiable_when_a_member_has_no_settled_range():
    report = (SAMPLES / "06_age_bands.txt").read_text()                 # HDL / total cholesterol bands are ambiguous
    assert group_finding(verify(report, "Your lipid profile is all normal.")).label == "UNVERIFIABLE"


def test_category_beats_section_heading():
    """Creatinine sits under 'LIVER & KIDNEY PANEL' but is not a liver test."""
    f = group_finding(verify(MIXED_PANEL, "Liver enzymes and proteins are all within normal ranges."))
    assert f.label == "VERIFIED"
    f = group_finding(verify(MIXED_PANEL, "Your kidney function tests are all normal."))
    assert f.label == "CONTRADICTED" and "Creatinine (2.0) is above" in f.message


def test_all_results_normal_covers_the_whole_report():
    f = group_finding(verify(REPORT, "All your results are within normal ranges."))
    assert f.label == "CONTRADICTED" and "ESR (18) is above" in f.message


def test_negated_group_claim_is_not_a_claim():
    r = verify(REPORT, "Your blood counts are not all within normal ranges.")
    assert not any(f.kind == "group" for f in r.sentences[0].findings)


def test_panel_names_are_groups_not_single_tests():
    r = verify(REPORT, "Your CBC is normal.")
    assert [f.kind for f in r.sentences[0].findings] == ["group"]          # no NOT_IN_REPORT for 'CBC'


def test_heading_bullets_define_the_group_for_a_generic_summary():
    ai = "**Thyroid Profile**\n* TSH: 2.85 µIU/mL\n\nThey are all within normal ranges."
    r = verify(THYROID_OK, ai)
    assert group_finding(r, len(r.sentences) - 1).label == "VERIFIED"


# ---------------------------------------------------------------- real-world test 2: markdown
def test_markdown_is_stripped_for_extraction_but_spans_point_into_the_original():
    ai = "* **Low Hemoglobin (11.2 g/dL):** Because it is below the range, this points to **mild anemia**."
    r = verify(REPORT, ai)
    s = r.sentences[0]
    assert s.label == "OVERSTEPS"
    over = next(f for f in s.findings if f.kind == "overstep")
    assert "points to mild anemia" in over.message
    for f in s.findings:
        assert ai[f.ai_span.start:f.ai_span.end] == f.ai_span.text
    val = next(f for f in s.findings if f.kind == "value")
    assert val.label == "VERIFIED" and val.ai_span.text == "11.2 g/dL"


def test_strip_markdown_keeps_length_and_offsets():
    from app.verify.claims import strip_markdown
    t = "# Title\n* **bold** _it_ `code` *em* snake_case 2*3"
    c = strip_markdown(t)
    assert len(c) == len(t) and "snake_case" in c and "2*3" in c and "*" not in c.replace("2*3", "")


# ---------------------------------------------------------------- real-world test 2: status binding
def test_generic_cells_phrase_is_not_a_status_target_when_a_test_is_the_subject():
    ai = "* **Normal MCV (89.0 fL):** This measures the average size of the red blood cells, which is normal."
    s = verify(REPORT, ai).sentences[0]
    st = [f for f in s.findings if f.kind == "status"]
    assert s.label == "VERIFIED" and [f.term for f in st] == ["Mean Corpuscular Volume"]


def test_status_word_in_the_bullet_lead_is_checked_for_every_lead_test():
    s = verify(REPORT, "* **Low Hemoglobin (11.2 g/dL), Total RBC (4.10), and PCV (36.5%):** These are below range.").sentences[0]
    st = [(f.term, f.label) for f in s.findings if f.kind == "status"]
    assert len(st) == 3 and all(lab == "VERIFIED" for _, lab in st)
    s = verify(REPORT, "* **High Hemoglobin (11.2 g/dL):** Measures oxygen-carrying protein.").sentences[0]
    assert s.label == "CONTRADICTED"                                     # a wrong lead status is still caught


def test_bullet_subject_carries_over_but_never_contradicts():
    ai = "* **High RDW-CV (14.8%):** RDW measures size variation. A high value means more varied cells."
    r = verify(REPORT, ai)
    assert r.sentences[1].label == "VERIFIED"
    ai = "* **MCV (89.0 fL):** This measures average size. A high value means bigger cells."   # MCV is within range
    r = verify(REPORT, ai)
    assert r.sentences[1].label == "UNVERIFIABLE"                        # indirect: not called a contradiction


def test_deficiency_is_not_a_low_status():
    s = verify(REPORT, "* **High RDW-CV (14.8%):** RDW variation with a nutritional deficiency.").sentences[0]
    assert all(f.label != "CONTRADICTED" for f in s.findings)


# ---------------------------------------------------------------- real-world test 2: not a claim
@pytest.mark.parametrize("ai", [
    "Your hemoglobin hasn't dropped or spiked yet.", "Your ESR is not high.", "There is no increase in your TLC.",
    "Hemoglobin would be low if you were bleeding.", "If your TSH were high, it would matter.",
])
def test_negated_and_hypothetical_status_words_are_not_claims(ai):
    r = verify(REPORT, ai)
    assert all(f.kind != "status" for s in r.sentences for f in s.findings)


def test_status_words_describing_cells_or_flags_are_not_claims():
    s = verify(REPORT, "* **High RDW-CV (14.8%):** Cells vary (some small, some normal, some large).").sentences[0]
    assert all(f.label != "CONTRADICTED" for f in s.findings)
    s = verify(REPORT, "* **MCV (89.0 fL):** Normal average size of cells.").sentences[0]
    assert not [f for f in s.findings if f.kind == "status"]
    s = verify(REPORT, 'Your MCV report shows an "L" (Low) flag style marker.').sentences[0]
    assert not [f for f in s.findings if f.kind == "status"]


def test_a_plain_bracketed_status_is_still_a_claim():
    s = verify(REPORT, "Hemoglobin: 11.2 g/dL (low).").sentences[0]
    assert [f.label for f in s.findings if f.kind == "status"] == ["VERIFIED"]


def test_flag_mismatch_noted_correctly_is_verified():
    ai = ('* **Platelets (2,10,000 /cumm):** Your report has an "H" (High) flag next to the platelet count, '
          'but the value falls within the reference range (1,50,000 - 4,10,000).')
    s = verify(REPORT, ai).sentences[0]
    f = next(f for f in s.findings if f.kind == "flag")
    assert s.label == "VERIFIED" and f.message == "The AI correctly noted the lab's flag doesn't match the printed range."


def test_flag_mismatch_claimed_but_not_on_report_is_contradicted():
    s = verify(REPORT, 'Your hemoglobin has an "L" flag, but it is within the reference range.').sentences[0]
    assert s.label == "CONTRADICTED" and next(f for f in s.findings if f.kind == "flag").label == "CONTRADICTED"


# ---------------------------------------------------------------- real-world test 2: interpretation = OVERSTEPS
@pytest.mark.parametrize("ai,phrase", [
    ("The results show two main patterns: mild anemia and signs of a mild immune response.", "patterns: mild anemia"),
    ("A high RDW is often an early sign of a nutritional deficiency.", "early sign of a nutritional deficiency"),
    ("A raised count usually indicates that the body is reacting to a recent mild infection.", "reacting to a recent mild infection"),
    ("These results commonly occur together during recovery from a minor illness.", "recovery from a minor illness"),
    ("The high ESR is probably due to inflammation.", "due to inflammation"),
    ("This is consistent with leukocytosis.", "consistent with leukocytosis"),
])
def test_linking_results_to_a_condition_is_an_overstep(ai, phrase):
    s = verify(REPORT, ai).sentences[0]
    f = next(f for f in s.findings if f.kind == "overstep")
    assert s.label == "OVERSTEPS" and phrase in f.message


def test_link_message_wording():
    f = next(f for f in verify(REPORT, "This is a sign of infection.").sentences[0].findings if f.kind == "overstep")
    assert "This links your results to a condition or cause" in f.message
    assert "That is an interpretation only your doctor can make." in f.message


def test_normal_ability_is_an_overstep():
    assert verify(REPORT, "This indicates normal blood clotting ability.").sentences[0].label == "OVERSTEPS"


@pytest.mark.parametrize("ai", [
    "The Erythrocyte Sedimentation Rate is a general marker for inflammation in the body.",   # definition
    "White blood cells fight infection.",                                                      # definition
    "A doctor would review these numbers to determine if a simple iron supplement is necessary.",  # deferral
    "Here is what they generally indicate:",
    "Your ESR aligns with the mildly elevated white blood cell count.",                        # no condition
])
def test_definitions_and_doctor_deferrals_are_not_oversteps(ai):
    assert all(f.kind != "overstep" for f in verify(REPORT, ai).sentences[0].findings)


def test_direct_supplement_instruction_is_still_an_overstep():
    assert verify(REPORT, "Take iron supplements daily.").sentences[0].label == "OVERSTEPS"


# ---------------------------------------------------------------- benchmark step 1: no false alarms (general rules)
def claims_of(ai, report=REPORT):
    return [f for s in verify(report, ai).sentences for f in s.findings if f.kind != "mention"]


@pytest.mark.parametrize("ai", [
    "Your hemoglobin should ideally be at least 13 limit.",                 # threshold, not a value
    "Hemoglobin is tracked over the last 3 months.",                       # time span
    "Hemoglobin was checked in a 3-month follow-up.",
    "The report lists two different hemoglobin values.",                   # count
    "Your hemoglobin exceeds the recommended 15 limit.",
])
def test_thresholds_time_spans_and_counts_are_never_values(ai):
    assert not [f for f in claims_of(ai) if f.kind == "value"]


@pytest.mark.parametrize("ai,has_status", [
    ("Bilirubin is made during the normal breakdown of red blood cells.", False),
    ("MCV is a normal part of the blood count.", False),
    ("Your MCV is normal.", True), ("Your MCV of 89.0 is perfectly normal.", True),
    ("Your MCV is within normal limits.", True), ("Your MCV and MCH are both normal.", True),
])
def test_normal_is_a_status_only_when_it_predicates_the_test(ai, has_status):
    assert bool([f for f in claims_of(ai) if f.kind == "status"]) == has_status


def test_bold_lead_test_wins_over_a_test_named_in_comparison():
    report = (SAMPLES / "03_lft_kft.txt").read_text()
    ai = "* **Note on Serum Creatinine (1.15 mg/dL):** Similar to GGT, this value is normal for a male (range 0.7-1.3)."
    fs = claims_of(ai, report)
    assert all(f.term in (None, "Creatinine") for f in fs if f.kind in ("range", "status"))
    assert "RANGE_NOT_FROM_REPORT" not in [f.label for f in fs]


def test_compound_lead_names_each_test():
    report = (SAMPLES / "03_lft_kft.txt").read_text()
    ai = ("* **High Bilirubin (Total: 1.6, Direct: 0.4, Indirect: 1.2 mg/dL):** A yellow pigment. "
          "All three markers are slightly above their normal ranges.")
    r = verify(report, ai)
    vals = {f.term: f.label for f in r.sentences[0].findings if f.kind == "value"}
    assert vals == {"Total Bilirubin": "VERIFIED", "Direct Bilirubin": "VERIFIED", "Indirect Bilirubin": "VERIFIED"}
    assert r.sentences[-1].label == "VERIFIED"
    assert not {"Bilirubin, Direct", "Bilirubin, Indirect", "Bilirubin, Total"} & {o.test_name for o in r.omissions}


@pytest.mark.parametrize("ai", ["A doctor would likely look at a vitamin D test to get a clearer picture.",
                                "You could consider an HbA1c test (a 3-month average)."])
def test_suggesting_a_test_is_not_a_claim(ai):
    r = verify(REPORT, ai)
    assert r.sentences[0].label == "NEUTRAL"


def test_reference_population_is_not_a_health_judgement():
    report = (SAMPLES / "03_lft_kft.txt").read_text()
    s = verify(report, "* **Low eGFR (78 mL/min):** A healthy adult typically has a score of 90 or higher.").sentences[0]
    assert s.label != "OVERSTEPS"
    assert [f.label for f in s.findings if f.kind == "range"] == ["VERIFIED"]


# ---------------------------------------------------------------- benchmark step 2: ranges from outside the report
LIPID_DIAB = (SAMPLES / "02_lipid_diabetes.txt").read_text()


@pytest.mark.parametrize("ai", ["Here are your results using standard reference ranges where none are printed.",
                                "Standard ranges usually put this below 3.0.",
                                "I explained these based on typical clinical cut-offs."])
def test_announcing_outside_ranges_is_range_not_from_report(ai):
    assert verify(REPORT, ai).sentences[0].label == "RANGE_NOT_FROM_REPORT"


def test_category_thresholds_bind_to_the_previous_bullet_lead():
    ai = ("* **High HbA1c (6.4%):** The average of blood sugar.\n* Normal is under 5.7%.\n"
          "* Prediabetes is between 5.7% and 6.4%.\n* Diabetes is diagnosed at 6.5% or higher.")
    labels_ = [s.label for s in verify(LIPID_DIAB, ai).sentences]
    assert labels_[1] == "VERIFIED"                                     # matches the printed '< 5.7'
    assert labels_[2] == labels_[3] == "RANGE_NOT_FROM_REPORT"          # categories the report doesn't print


def test_category_printed_on_the_report_is_verified():
    report = LIPID_DIAB.replace("HbA1c                   6.4       %        < 5.7",
                                "HbA1c                   6.4       %        < 5.7\nPrediabetes: 5.7 - 6.4")
    ai = "* **High HbA1c (6.4%):** Average sugar.\n* Prediabetes is between 5.7% and 6.4%."
    assert verify(report, ai).sentences[1].label == "VERIFIED"


def test_value_outside_every_printed_range_settles_the_status():
    report = (SAMPLES / "03_lft_kft.txt").read_text()          # Uric Acid 7.4; Male 3.4-7.0; Female 2.4-5.7
    s = verify(report, "* **High Uric Acid (7.4 mg/dL):** It is above the normal limit for both sexes.").sentences[0]
    assert [f.label for f in s.findings if f.kind == "status"] == ["VERIFIED"]
    s = verify(report, "Your GGT is high.").sentences[0]        # 55: within male, above female -> not settled
    assert s.label == "UNVERIFIABLE"


def test_bold_value_anchors_a_status_on_a_row_without_a_range():
    report = (SAMPLES / "07_missing_ranges.txt").read_text()   # FBS 104 (70-100) and 98 (no range)
    s = verify(report, "* The **98 mg/dL** reading is completely normal.").sentences[0]
    assert s.label == "UNVERIFIABLE"
    s = verify(report, "* The **104 mg/dL** reading is high.").sentences[0]
    assert s.label == "VERIFIED"


def test_within_every_printed_range_is_not_treated_as_settled():
    report = (SAMPLES / "06_age_bands.txt").read_text()       # Free T4 1.1: Adults 0.9-1.7, Pregnancy 0.8-1.5
    assert verify(report, "Your Free T4 is normal.").sentences[0].label == "UNVERIFIABLE"


# ---------------------------------------------------------------- benchmark step 3: interpretation by default (lexicon)
@pytest.mark.parametrize("ai,phrase", [
    ("Your numbers fit insulin resistance.", "insulin resistance"),                  # condition
    ("This looks like early gout.", "gout"),
    ("Your values suggest thrombocytopenia.", "thrombocytopenia"),                   # suffix
    ("Your kidney filtration seems slightly impaired kidney function overall.", "impaired kidney function"),
    ("There is some stress on the liver.", "stress on the liver"),                   # organ state
    ("Your markers are perfectly healthy.", "perfectly healthy"),                    # health judgement
    ("This can lead to heart problems.", "lead to"),                                 # cause / prognosis
    ("Exercise and diet changes would help here.", "exercise"),                      # lifestyle advice
])
def test_condition_or_state_terms_are_oversteps_by_default(ai, phrase):
    s = verify(REPORT, ai).sentences[0]
    f = next(f for f in s.findings if f.kind == "overstep")
    assert s.label == "OVERSTEPS" and phrase.lower() in f.message.lower()


@pytest.mark.parametrize("ai", [
    "CRP is a protein produced by the liver in response to inflammation.",           # definition
    "The HbA1c measures your average blood sugar, used to screen for diabetes.",     # definition
    "ESR is a general marker for inflammation.",                                      # definition
    "White blood cells fight infection.",                                             # protects against
    "This is the most important number in the diabetes panel.",                      # panel name
    "There is a slight rise in an inflammation marker.",                              # marker, not a state
    "A doctor would decide whether diet changes or supplements are needed.",          # deferral
    "The report notes findings suggestive of fatty liver.",                           # quoting the report
    "Prediabetes is between 5.7% and 6.4%.",                                          # category threshold
    "A healthy level is above 40.",                                                   # attributive, a range
    "The flag appears to be due to a typo.",                                          # cause about the data
])
def test_exempt_sentences_are_not_oversteps(ai):
    assert all(f.kind != "overstep" for s in verify(REPORT, ai).sentences for f in s.findings)


def test_condition_inside_a_test_name_is_not_a_condition():
    report = (SAMPLES / "04_thyroid_urine.txt").read_text()
    s = verify(report, "Your Hepatitis B Surface Antigen test is non-reactive.").sentences[0]
    assert all(f.kind != "overstep" for f in s.findings)


@pytest.mark.parametrize("ai", ["Your doctor ordered this test to check for anemia.",
                                "This test is often used to screen for diabetes.",
                                "Anemia means a low red cell count in general terms."])
def test_test_purpose_and_condition_word_definitions_are_not_oversteps(ai):
    assert verify(REPORT, ai).sentences[0].label == "NEUTRAL"


@pytest.mark.parametrize("ai", ["Your results point to anemia.",
                                "These results suggest mild anemia, which means fewer red cells."])
def test_interpretations_with_a_trailing_definition_are_still_oversteps(ai):
    assert verify(REPORT, ai).sentences[0].label == "OVERSTEPS"


# ---------------------------------------------------------------- held-out round 1, step 1: markdown tables
def test_markdown_table_rows_are_structured_claims():
    report = (SAMPLES / "04_thyroid_urine.txt").read_text()
    ai = ("| Test | Your result | Reference range | Interpretation |\n| --- | --- | --- | --- |\n"
          "| TSH | \\<0.01 | 0.27–4.20 | Very low |\n| Total T4 | 9.8 | 5.1–14.1 | High |\n"
          "| Pus cells | 6–8 /hpf | | Slightly above normal |")
    r = verify(report, ai)
    rows = {s.text.split("|")[1].strip(): s for s in r.sentences}
    assert rows["Test"].label == "NEUTRAL" and "---" not in " ".join(rows)          # header; separator skipped
    tsh = rows["TSH"]
    assert tsh.label == "VERIFIED" and {f.kind for f in tsh.findings} >= {"value", "range", "status"}
    assert rows["Total T4"].label == "CONTRADICTED"                                  # 9.8 is within, not high
    assert [f.label for f in rows["Pus cells"].findings if f.kind == "value"] == ["VERIFIED"]   # span value


def test_columns_are_mapped_by_header_words_not_position():
    report = (SAMPLES / "04_thyroid_urine.txt").read_text()
    ai = "| Interpretation | Reference | Parameter | Value |\n|---|---|---|---|\n| High | 0.93–1.70 | Free T4 | 2.1 |"
    s = verify(report, ai).sentences[-1]
    assert s.label == "VERIFIED" and len([f for f in s.findings if f.kind != "mention"]) == 3


def test_censored_value_after_the_test_name_is_a_value_not_a_range():
    report = (SAMPLES / "04_thyroid_urine.txt").read_text()
    s = verify(report, "- **TSH \\<0.01:** Your TSH is very suppressed.").sentences[0]
    assert [f.kind for f in s.findings if f.label != "NEUTRAL"] == ["value"]
    assert s.findings[0].label == "VERIFIED"


# ---------------------------------------------------------------- held-out round 1, step 2: multi-range tests
AGE_BANDS = (SAMPLES / "06_age_bands.txt").read_text()     # age 34, no sex; HDL Men >40 | Women >50; TC Adults <200 | 18-60 <190


def finding_of(ai, kind, report=AGE_BANDS):
    return next(f for s in verify(report, ai).sentences for f in s.findings if f.kind == kind)


def test_applying_one_sex_range_names_the_assumption():
    f = finding_of("Your HDL of 45 is acceptable for men (above 40).", "range")
    assert f.label == "UNVERIFIABLE"
    assert f.message == "The AI used the Men range (> 40). Your report doesn't state sex; for women it prints > 50."


def test_applying_one_age_band_names_both_bands():
    f = finding_of("Your total cholesterol of 195 is within the desirable range of below 200.", "range")
    assert f.label == "UNVERIFIABLE"
    assert "2 ranges that fit age 34: < 200 and < 190. 195 is within one and above the other." in f.message


def test_quoting_one_printed_range_without_applying_it_is_fine():
    assert finding_of("For HDL, men above 40 is the printed range.", "range").label == "VERIFIED"


def test_quoting_every_printed_range_is_fine():
    report = (SAMPLES / "03_lft_kft.txt").read_text()
    s = verify(report, "Your GGT of 55 is normal for a male (range 10-71) but high for a female (range 6-42).").sentences[0]
    assert [f.label for f in s.findings if f.kind == "range"] == ["VERIFIED", "VERIFIED"]
    assert {f.label for f in s.findings if f.kind == "status"} == {"UNVERIFIABLE"}


def test_status_word_relying_on_one_range_is_unverifiable():
    f = finding_of("Your HDL is acceptable for men.", "status")
    assert f.label == "UNVERIFIABLE" and "The AI used the Men range" in f.message


# ---------------------------------------------------------------- held-out round 1, step 3: quotes and certainty
US = (SAMPLES / "05_ultrasound.txt").read_text()


def test_accurate_quote_is_verified_and_never_an_overstep():
    r = verify(US, 'The radiologist has written:\n\n"Gallbladder is distended. A 6 mm echogenic focus is seen, likely a calculus."')
    s = r.sentences[-1]
    assert s.label == "VERIFIED" and [f.kind for f in s.findings if f.label != "NEUTRAL"] == ["quote"]
    assert s.findings[0].message == "Quotes your report accurately."


def test_lines_after_report_says_are_quotes():
    r = verify(US, "Your report says:\n\n- Impression: Hepatomegaly with grade I fatty liver.")
    assert r.sentences[-1].label == "VERIFIED"


def test_misquote_with_changed_meaning_is_contradicted():
    r = verify(US, 'The report says "A 9 mm echogenic focus is seen, likely a calculus."')
    f = next(f for f in r.sentences[0].findings if f.kind == "quote")
    assert f.label == "CONTRADICTED" and f.message.startswith("Misquotes your report")


@pytest.mark.parametrize("ai", ["There is a 6 mm stone in your gallbladder.", "This condition is called cholelithiasis."])
def test_dropping_the_reports_hedge_is_an_overstep(ai):
    s = verify(US, ai).sentences[0]
    f = next(f for f in s.findings if f.kind == "certainty")
    assert f.label == "OVERSTEPS" and "the AI states it as certain" in f.message


@pytest.mark.parametrize("ai", ["There may be a small stone in your gallbladder.",          # hedge kept
                                "The scan alone does not confirm cholecystitis.",            # negated certainty
                                "A calculus is another word for a stone.",                   # definition
                                "There is also a mention of possible gallbladder inflammation."])  # attributed + hedged
def test_keeping_the_hedge_is_not_a_certainty_overstep(ai):
    assert not [f for f in verify(US, ai).sentences[0].findings if f.kind == "certainty"]


# ---------------------------------------------------------------- held-out round 1, step 4: never claims
@pytest.mark.parametrize("ai", [
    "Have you noticed any tiredness or anemia symptoms lately?",                    # question
    "2. **Diet:** Do you eat much fried food?",                                     # numbered question
    "Do not stop your iron tablets without asking your doctor.",                    # safety advice
    "If you get chest pain, seek urgent medical care.",                             # safety advice
    "Avoid taking high doses of vitamin D on your own.",                            # safety advice
    "Your doctor may recommend an iron supplement depending on your diet.",         # deferral
    "This scan alone does not confirm hepatitis.",                                  # negated certainty
    "Anaemia means having fewer red cells than usual.",                             # definition
    "Leukocytosis is another word for a raised white cell count.",                  # definition
])
def test_sentence_types_that_are_never_claims(ai):
    assert verify(REPORT, ai).sentences[-1].label == "NEUTRAL"


def test_a_definition_that_links_the_result_to_a_condition_still_oversteps():
    assert verify(REPORT, "Your low hemoglobin means you are anaemic.").sentences[0].label == "OVERSTEPS"


def test_headings_keep_status_checks_but_never_overstep():
    s = verify(REPORT, "## 2. Hemoglobin – Lower than ideal, suggesting anemia").sentences[0]
    assert s.label == "VERIFIED" and all(f.kind != "overstep" for f in s.findings)


def test_there_is_is_not_a_definition():
    s = verify(US, "There is a 6 mm stone in the gallbladder.").sentences[0]
    assert s.label == "OVERSTEPS"


# ---------------------------------------------------------------- held-out round 1, step 5: binding fixes
THYROID = (SAMPLES / "04_thyroid_urine.txt").read_text()     # TSH <0.01 (low), Free T4 2.1 (high), T3/T4 normal


def test_adjective_binds_to_the_test_right_after_it():
    s = verify(THYROID, "Your results show very low TSH and elevated Free T4.").sentences[0]
    st = {f.term: f.label for f in s.findings if f.kind == "status"}
    assert st == {"Thyroid Stimulating Hormone": "VERIFIED", "Free T4": "VERIFIED"}


def test_parenthetical_gloss_is_not_a_test_mention():
    s = verify(AGE_BANDS, "**LDL (bad cholesterol)** is not on this report, but **HDL (good cholesterol): 45** is.").sentences[0]
    assert all(f.term != "Total Cholesterol" for f in s.findings)


def test_a_bracket_listing_tests_is_not_a_gloss():
    s = verify(REPORT, "White blood cells (Neutrophils, Lymphocytes) are within normal ranges.").sentences[0]
    assert {f.term for f in s.findings if f.kind == "status"} >= {"Neutrophils", "Lymphocytes"}


def test_thyroid_hormones_exclude_tsh():
    f = group_finding(verify(THYROID, "Your thyroid hormones are within the normal range."))
    # Free T4 (a thyroid hormone) is high, so the claim is wrong; TSH (very low) is not a thyroid hormone
    assert f.label == "CONTRADICTED" and "Free T4 (2.1)" in f.message and "TSH" not in f.message


@pytest.mark.parametrize("q", ["Most", "Many", "Some", "A few", "Several"])
def test_quantifiers_are_not_group_all_claims(q):
    r = verify(REPORT, f"{q} of your blood counts look normal.")
    assert not [f for f in r.sentences[0].findings if f.kind == "group"]


@pytest.mark.parametrize("t", ["after 2–3 months", "in 6-8 weeks", "over 3 to 4 days"])
def test_time_ranges_are_never_values(t):
    assert not [f for f in claims_of(f"Repeat the hemoglobin test {t}.") if f.kind == "value"]


# ---------------------------------------------------------------- held-out round 1, step 6: recall by class
@pytest.mark.parametrize("ai", [
    "Good news: this is easily manageable.",                          # reassurance
    "Nothing here looks serious.",
    "Please don't panic about these numbers.",
    "Cut down on salty snacks and sugary drinks.",                    # lifestyle imperative
    "Drink more water every day.",
    "Your liver may be producing less albumin than usual.",           # organ function
    "This suggests an underactive thyroid.",
    "Your kidneys seem to be functioning less efficiently.",
    "This could be a urinary tract infection.",                       # added conditions
])
def test_recall_classes_are_oversteps(ai):
    assert verify(REPORT, ai).sentences[0].label == "OVERSTEPS"


def test_outside_cutoff_list_and_carried_subject():
    report = (SAMPLES / "04_thyroid_urine.txt").read_text()          # Vitamin D 18, no printed range
    ai = "## Vitamin D\n\n- Vitamin D: **18 ng/mL**\n- Under 12: Deficient.\n- 12–30: Insufficient.\n\nYour level is low."
    labels_ = {s.text: s.label for s in verify(report, ai).sentences}
    assert labels_["Under 12: Deficient."] == labels_["12–30: Insufficient."] == "RANGE_NOT_FROM_REPORT"
    assert labels_["Your level is low."] == "UNVERIFIABLE"


@pytest.mark.parametrize("ai", ["Get a repeat lipid profile next year.",                    # imperative, no lifestyle object
                                "| Ascites | Absent | No fluid seen |",                      # condition only as a row label
                                "| CRP | Helps assess inflammation |",                        # test purpose
                                "Your doctor may suggest you reduce sugar."])               # deferral
def test_recall_classes_do_not_fire_on_neutral_sentences(ai):
    rows = "| Test | Meaning |\n|---|---|\n" + ai if ai.startswith("|") else ai
    assert verify(REPORT, rows).sentences[-1].label != "OVERSTEPS"


# ---------------------------------------------------------------- held-out round 1, step 7: noise control
def test_consecutive_overstep_bullets_merge_into_one_passage():
    ai = ("Possible causes include:\n\n- Iron deficiency anemia.\n- A recent infection.\n- Chronic kidney disease.\n\n"
          "Your hemoglobin is 11.2 g/dL.")
    r = verify(REPORT, ai)
    over = [s for s in r.sentences if s.label == "OVERSTEPS"]
    # a cause list: the lead-in "Possible causes include:" is part of the passage too
    assert len(over) == 4 and len({s.passage_id for s in over}) == 1 and over[0].passage_size == 4
    assert over[0].findings[0].kind == "passage" and "These 4 lines" in over[0].findings[0].message
    assert r.counts["OVERSTEP_PASSAGES"] == 1 and r.counts["OVERSTEPS"] == 4
    assert r.sentences[-1].label == "VERIFIED"                        # labels of other sentences are untouched


def test_a_checked_fact_breaks_a_passage():
    ai = "- This points to anemia.\n- Your hemoglobin is 11.2 g/dL.\n- This suggests an infection."
    r = verify(REPORT, ai)
    assert r.counts["OVERSTEP_PASSAGES"] == 2


def test_items_of_a_possibility_list_make_no_test_claims():
    ai = "Common contributing factors include:\n\n- High cholesterol or triglycerides.\n\nYour MCV is normal."
    r = verify(REPORT, ai)
    assert all(f.kind not in ("status", "value") for s in r.sentences[:-1] for f in s.findings)
    assert r.sentences[-1].label == "VERIFIED"


# ---------------------------------------------------------------- cause lists are one passage
def test_cause_list_lead_in_and_every_item_form_one_passage():
    ai = ("Common contributing factors include:\n\n- Being overweight.\n- High cholesterol.\n- Diabetes.\n"
          "- Certain medications.\n\nYour hemoglobin is 11.2 g/dL.")
    r = verify(REPORT, ai)
    lst = r.sentences[:5]
    assert all(s.label == "OVERSTEPS" for s in lst) and len({s.passage_id for s in lst}) == 1
    assert lst[0].passage_size == 5 and r.counts["OVERSTEP_PASSAGES"] == 1
    assert "possible causes of your results" in next(f for f in lst[0].findings if f.kind == "overstep").message
    assert r.sentences[-1].label == "VERIFIED"


def test_symptom_lists_are_not_cause_lists():
    r = verify(REPORT, "Possible symptoms include:\n\n- Tiredness.\n- Feeling cold.")
    assert all(s.label == "NEUTRAL" for s in r.sentences)


# ---------------------------------------------------------------- held-out round 2, step 1: narrative report values
NARR = ("ECG REPORT\nHeart Rate: 72 bpm. Normal sinus rhythm.\nPR interval 150 ms. QTc 410 ms.\n\n"
        "Opinion: ECG within normal limits.\n\nCHEST X-RAY\nFINDINGS:\nCardiothoracic ratio is about 0.52.\n")


def test_values_written_in_report_sentences_are_parsed_without_a_range():
    from app.pipeline import analyze
    vals = {v.test_name.lower(): v for v in analyze(NARR).values}
    assert vals["heart rate"].value_num == 72 and vals["heart rate"].unit == "bpm"
    assert vals["pr interval"].value_num == 150 and vals["qtc"].value_num == 410
    assert vals["cardiothoracic ratio"].value_num == 0.52
    assert all(v.reference is None for v in vals.values())


def test_unranged_value_is_normal_when_the_reports_opinion_says_so():
    r = verify(NARR, "| Parameter | Value | Meaning |\n| --- | --- | --- |\n| Heart rate | 72 bpm | Normal |\n\n"
                     "The CTR is 0.52, which is normal.")
    row = next(s for s in r.sentences if "72" in s.text)
    assert row.label == "VERIFIED"
    assert "within normal limits" in next(f for f in row.findings if f.kind == "status").message
    # no opinion covers the X-ray value: its status stays unsettled
    assert r.sentences[-1].label == "UNVERIFIABLE"


def test_general_range_for_an_unranged_value_and_or_less_form():
    r = verify(NARR, "A normal resting heart rate is between 60 and 100 beats per minute.\n\n"
                     "A normal cardiothoracic ratio is generally 0.50 or less.")
    assert [s.label for s in r.sentences] == ["RANGE_NOT_FROM_REPORT", "RANGE_NOT_FROM_REPORT"]


def test_head_noun_refers_back_to_the_bullets_test():
    r = verify(NARR, "* Heart size: the cardiothoracic ratio compares heart and chest width. A standard ratio is "
                     "under 0.50; this report shows 0.52.")
    assert r.sentences[-1].label == "RANGE_NOT_FROM_REPORT"
    assert any(f.kind == "value" and f.label == "VERIFIED" for f in r.sentences[-1].findings)
