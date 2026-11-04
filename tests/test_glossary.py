from app.glossary.matcher import find_terms, find_unrecognized, load_glossary


def ids(text):
    return [m.term_id for m in find_terms(text)]


def test_glossary_size_and_schema():
    gl = load_glossary()
    assert 120 <= len(gl) <= 450
    for e in gl.values():
        assert e["term"] and e["category"] and e["plain_explanation"] and e["source_note"]
        assert isinstance(e["aliases"], list)


def test_longest_match_first():
    assert ids("Direct Bilirubin 0.2") == ["direct_bilirubin"]
    assert ids("Bilirubin, Direct 0.2") == ["direct_bilirubin"]
    assert ids("HDL Cholesterol 45") == ["hdl_cholesterol"]
    assert ids("Fasting Blood Glucose") == ["fasting_blood_sugar"]


def test_aliases_map_to_canonical():
    assert ids("SGPT") == ["alanine_aminotransferase"]
    assert ids("SGOT") == ["aspartate_aminotransferase"]
    assert ids("Haemoglobin") == ids("Hb") == ["hemoglobin"]


def test_word_boundaries_no_match_inside_words():
    assert ids("SALT and basalt") == []        # ALT inside other words
    assert ids("HbA1c") == ["hba1c"]          # not Hb
    assert ids("Catastrophe") == []            # 'cast' inside a word


def test_case_insensitive_long_terms_case_sensitive_short():
    assert ids("HEMOGLOBIN") == ["hemoglobin"]
    assert ids("hb") == []                      # short abbreviations need report casing


def test_ambiguous_abbreviation_lists_possibilities_without_context():
    m = find_terms("PCV noted")[0]
    assert m.ambiguous and len(m.possibilities) == 2


def test_ambiguous_abbreviation_resolved_by_context():
    m = [x for x in find_terms("Hemoglobin 13  PCV 44") if x.term_id == "pcv"][0]
    assert not m.ambiguous and "Packed Cell Volume" in m.possibilities[0]


def test_spans_are_exact():
    text = "Serum Creatinine 1.1"
    m = find_terms(text)[0]
    assert text[m.span.start:m.span.end] == m.matched_text == "Serum Creatinine"


def test_unrecognized_terms_are_listed_not_explained():
    text = "Findings suggest osteomyelitis. QZX level noted."
    known = [(m.span.start, m.span.end) for m in find_terms(text)]
    u = {x.text for x in find_unrecognized(text, known)}
    assert {"osteomyelitis", "QZX"} <= u
    assert all("ask your clinician" in x.reason for x in find_unrecognized(text, known))


def test_known_terms_not_reported_as_unrecognized():
    text = "TSH 2.1 and hepatomegaly"
    known = [(m.span.start, m.span.end) for m in find_terms(text)]
    assert find_unrecognized(text, known) == []


def test_synthetic_banner_word_sample_is_not_a_term():
    assert ids("*** SYNTHETIC SAMPLE — not real patient data ***") == []
    assert ids("Sample Type: Serum") == ["specimen", "serum"]


# ---------------------------------------------------------------- abbreviation safety
def test_ct_is_case_sensitive():
    assert ids("CT scan of abdomen") == ["computed_tomography"]
    assert ids("ct") == [] and ids("Ct") == []
    assert ids("12 ct units") == []


def test_short_caps_not_inside_longer_tokens():
    assert "prothrombin_time" not in ids("PTH 45 pg/mL")             # PT inside PTH
    assert ids("PT/INR 1.1") and ids("PT/INR 1.1")[0] == "prothrombin_time"
    assert ids("ERA BRAIN") == []                                     # RA/ERA inside words
    assert ids("mm/1st hr") == []                                     # HR as a unit, lowercase


def test_longest_match_still_first_with_abbreviations():
    assert ids("CT Scan") == ["computed_tomography"]
    assert ids("Chest X-ray") == ["x_ray"]
    assert ids("Normal Sinus Rhythm") == ["sinus_rhythm"]


def test_no_duplicate_aliases_across_entries():
    owner = {}
    for e in load_glossary().values():
        for a in {x.strip().lower() for x in [e["term"], *e["aliases"]]}:
            assert a not in owner, (a, owner.get(a), e["id"])
            owner[a] = e["id"]


def test_no_forbidden_phrases_in_entries():
    import re
    bad = re.compile(r"you have|indicates|means you|diagnos|treat", re.I)
    for e in load_glossary().values():
        for f in ("plain_explanation", "what_it_is_not"):
            assert not bad.search(e.get(f) or ""), (e["id"], f)


def test_kinds_and_finding_note():
    from app.pipeline import FINDING_NOTE, analyze
    gl = load_glossary()
    assert {e["kind"] for e in gl.values()} <= {"test", "finding", "abbreviation", "report_word"}
    for tid in ("cholecystitis", "hepatomegaly", "malignant", "cardiomegaly"):
        assert gl[tid]["kind"] == "finding"
    r = analyze("IMPRESSION:\nHepatomegaly. Findings suggestive of cholecystitis.")
    cards = [e for e in r.explanations if e.origin == "glossary"]
    finding_cards = [e for e in cards if gl[e.related_id]["kind"] == "finding"]
    assert finding_cards and all(FINDING_NOTE in e.text for e in finding_cards)
    assert all(FINDING_NOTE not in e.text for e in cards if gl[e.related_id]["kind"] != "finding")
    # the Impression line itself stays NEEDS_PROFESSIONAL_INTERPRETATION
    assert any(e.tag.value == "NEEDS_PROFESSIONAL_INTERPRETATION" and e.title.startswith("IMPRESSION") for e in r.explanations)
