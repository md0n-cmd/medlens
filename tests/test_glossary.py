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
