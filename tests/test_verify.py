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
    f = group_finding(verify(THYROID_OK, "Your thyroid hormones are within standard reference intervals."))
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
