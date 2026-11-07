# Edge cases: handling and tests

| # | Edge case | Handling | Test(s) |
|---|---|---|---|
| 1 | En dash / em dash / `to` in range | Accepted as interval separator | `test_ranges.py::test_interval_formats` |
| 2 | Brackets `(13.0-17.0)`, `[4-11]` | Stripped before parsing | `test_interval_formats` |
| 3 | Indian `1,50,000` / Western `150,000` grouping | Comma groups of 2 or 3 digits removed | `test_interval_formats`, `test_values.py::test_thousand_separator_value`, `test_cbc_sample` |
| 4 | Scientific units `x10^3/uL`, `10^9/L` | Kept as unit text, compared as-is | `test_interval_with_scientific_unit` |
| 5 | `<200` vs value 200 | Exclusive → **above_range** | `test_exclusive_upper_boundary_is_out`, `test_lipid_diabetes_sample_boundaries` (TG 150 vs `< 150`) |
| 6 | `Up to 40` / `Upto 40` vs 40 | Inclusive → within | `test_inclusive_boundaries_are_within`, `test_lft_kft_sample_ambiguity_and_units` (SGOT 40) |
| 7 | `>40` vs 40; `≥ 60` vs 60 | Exclusive → below; inclusive → within | `test_exclusive_lower_boundary_is_out` |
| 8 | Censored value `<0.01` | Interval comparison: entirely below → below; overlapping → indeterminate | `test_censored_values`, `test_thyroid_urine_sample_qualitative_and_censored` |
| 9 | Range-valued result `6-8 /hpf` | Interval `[6,8]` compared with range | `test_thyroid_urine_sample_qualitative_and_censored` (Pus Cells) |
| 10 | No range printed | `no_reference_range_supplied`, shown grey, never flagged | `test_value_without_range`, `test_lipid_diabetes_sample_boundaries` (Non-HDL) |
| 11 | Sex-specific `M: 0.7-1.3 F: 0.6-1.1` (one or several columns) | `ambiguous_range` + "ask your clinician which applies" | `test_sex_specific_ranges_are_multi`, `test_lft_kft_sample_ambiguity_and_units` |
| 12 | Report header states `Sex: Male` | Matching alternative used, with a note | `test_select_alternative_requires_header_sex`, `test_sex_in_header_selects_range` |
| 13 | Unit mismatch (`mEq/L` value vs `mmol/L` range) | `unit_mismatch`; no conversion; not flagged | `test_unit_compatibility`, `test_lft_kft_sample_ambiguity_and_units` (Chloride) |
| 14 | Cosmetic unit variants `µIU/mL` vs `uIU/ml` | Treated as equal | `test_unit_compatibility` |
| 15 | Qualitative `Trace` vs `Negative` | `differs_from_reference` | `test_qualitative_comparison`, Urine Protein in sample 4 |
| 16 | `Non-reactive` vs `Non reactive`, `Nil` vs `Absent` | Same normalised word / equivalence group → within | `test_qualitative_comparison` |
| 17 | Lab flag `H` on a within-range value | `flag_disagreement` + PROFESSIONAL card | `test_cbc_sample` (Platelet Count) |
| 18 | Out-of-range value with no lab flag | Status computed; note "report does not print a flag" | `test_cbc_sample` (ESR) |
| 19 | Flags attached `33.1*`, `11.2L`, separate column `H`, after unit | All recognised | `test_attached_and_star_flags`, `test_columnar_row_with_flag_column` |
| 20 | Range text unreadable (`see note`) | `unparseable`; not flagged | `test_unparseable`, `test_empty_and_garbage_input` |
| 21 | Reversed interval `17 - 13` | `unparseable` (never guessed) | `test_unparseable` |
| 22 | Patient metadata lines (`Age: 45 Years`) | Not treated as results | `test_non_result_lines_ignored` |
| 23 | Radiology measurements (`10.2 x 4.5 cm`) | Not lab values | `test_non_result_lines_ignored`, `test_ultrasound_sample_interpretive_sections` |
| 24 | Impression/Advice/Remarks sections | Every line → NEEDS_PROFESSIONAL; never parsed as values | `test_sections_and_interpretive_flag`, `test_impression_section_needs_professional_even_without_hedges` |
| 25 | Hedged phrasing (`suggestive of`, `?early`, `cannot be ruled out`) | NEEDS_PROFESSIONAL | `test_hedged_language_needs_professional` |
| 26 | Several abnormal values | Combination card: only a clinician can interpret patterns | `pipeline.py` (visible in samples 1–3) |
| 27 | Longest match (`Direct Bilirubin` vs `Bilirubin`) | Longest alias wins | `test_longest_match_first` |
| 28 | Abbreviation inside a word (`SALT`, `HbA1c`) | Word boundaries | `test_word_boundaries_no_match_inside_words` |
| 29 | Ambiguous abbreviation `PCV` | Context keywords resolve it; otherwise all meanings listed | `test_ambiguous_abbreviation_*` |
| 30 | Unknown term (`cholecystitis`, `QZX`) | Listed as "unrecognized: ask your clinician"; no meaning invented | `test_unrecognized_terms_are_listed_not_explained` |
| 31 | Diagnostic/treatment wording in generated text | Replaced by safe fallback | `test_safety_blocks_diagnostic_and_treatment_phrasing` |
| 32 | Glossary content safety | All 352 entries must pass the filter | `test_every_glossary_entry_passes_safety_filter` |
| 33 | CRLF line endings | Normalised; offsets match the returned text | `test_crlf_input_normalised` |
| 34 | Empty input | HTTP 400 | `test_api.py::test_analyze_rejects_empty` |
| 35 | Text with no medical content | Warning; empty results | `test_empty_and_garbage_input` |
| 36 | Span integrity | `text[start:end] == span.text` for all spans in all samples | `test_all_spans_and_text_safe[*]` |
| 37 | Text PDF vs scanned PDF | Text layer is used when a page has ≥ 20 chars; otherwise the page is rendered at 300 DPI and OCR'd (`source` = `pdf_text` / `pdf_ocr`) | `test_ingest.py::test_text_pdf_matches_txt_exactly`, `test_scanned_pdf_uses_ocr` |
| 38 | PDF table columns collapsing | Lines rebuilt from word x-positions so the 2+ space column gaps survive; text PDF gives identical values and statuses to the .txt | `test_text_pdf_matches_txt_exactly` |
| 39 | OCR / PDF merging "unit  range" into one column | Parser splits a leading unit token off a range cell (`million/cumm 4.5 - 5.5`) | `test_clean_png_ocr_recovers_names_and_values` |
| 40 | Rotated / blurred / noisy phone photo | EXIF transpose, grayscale, upscale to ~2000 px, autocontrast, unsharp mask; confidence and uncertain tokens reported | `test_phone_photo_reports_low_confidence_tokens`, `test_api_extract_image` |
| 41 | OCR digit confusion (O/0, l/1, lost decimal point) | Never auto-analyzed: review banner lists uncertain tokens and asks the user to check; orange warning below 70% confidence. Real case: `HbA1c`→`HbAlc`, `< 5.7`→`<25.7`, both flagged | `test_scanned_pdf_uses_ocr` |
| 42 | Multi-page PDFs | All pages extracted; mixed text and scanned pages get a warning saying how many were OCR'd | `app/ingest/pdf.py` |
| 43 | Password-protected PDF | 422 "This PDF is password-protected…" | `test_password_protected_pdf_friendly_message` |
| 44 | Corrupt PDF | 422 with a friendly message | `test_corrupt_pdf_is_friendly`, `test_api_corrupt_pdf_422` |
| 45 | Wrong file type / renamed file | 415; extension AND magic bytes must agree | `test_api_rejects_bad_type` |
| 46 | File too large (> 10 MB) | 413 | `test_api_rejects_too_large` |
| 47 | Tesseract not installed | 503 "OCR engine not installed: sudo apt install tesseract-ocr"; PDF text still works; image and camera tabs disabled in UI | `test_health_reports_ocr`, tests skip via `skipif` |
| 48 | Camera permission denied / no camera | Falls back to `<input type="file" accept="image/*" capture="environment">` | frontend (manual) |
| 49 | Age-banded ranges (`Adults: 0.4-4.0 \| Children 1-5 yrs: 0.7-6.0`, `18-60 years`, `>60 Y`) | Parsed as labelled alternatives; the header's `Age` picks the band and the card says so | `test_age_bands.py::test_age_band_cell_parses_as_multi`, `test_header_age_picks_band` |
| 50 | Combined age + sex labels (`Adult male`) | Picked only when the header gives both age and sex | `test_age_and_sex_band` |
| 51 | Header missing the age (or sex) the bands need | `ambiguous_range`, options listed, "ask your clinician" | `test_missing_age_is_ambiguous`, `test_missing_sex_is_ambiguous_never_guessed` |
| 52 | Overlapping bands (`Adults` and `18-60 years` both match) | `ambiguous_range` (never pick one) | `test_overlapping_bands_are_ambiguous` |
| 53 | Pregnancy / trimester ranges | Always `ambiguous_range`, since MedLens cannot know pregnancy status | `test_pregnancy_band_on_continuation_line_is_ambiguous` |
| 54 | Labelled range continued on an indented next line | Merged into the row's range, with a note | `test_pregnancy_band_on_continuation_line_is_ambiguous` |
| 55 | Range cell is a placeholder (`-`, `--`, `NA`, `N/A`, `Nil` for a number, `See note`, `Refer table`, `As per clinical context`) | `no_reference_range_supplied` with reason `placeholder`; fixed wording; never flagged | `test_missing_ranges.py::test_every_missing_reason_is_covered` |
| 56 | Missing-range reasons distinguished | `no_range_printed`, `placeholder`, `range_unreadable`, `unit_mismatch`, `qualitative_no_reference`, shown in card, plain words and structured summary | `test_every_missing_reason_is_covered`, `test_summary_lists_missing_with_reasons` |
| 57 | Same test twice, only one row has a range | Never borrowed: other row gets "range printed for another row of this test; not applied" | `test_range_from_another_row_is_not_borrowed` |
| 58 | `Nil` as a genuine qualitative reference (e.g. Casts Absent / Nil) | Still compared qualitatively (not a placeholder) | `test_nil_is_still_a_valid_qualitative_reference` |
| 59 | Structured summary must not leak identity | Patient name never included; header has only lab, date, specimen, age, sex | `test_summary.py::test_summary_never_contains_patient_name` |
| 60 | Term search typos / aliases / no match | Fuzzy (difflib ≥ 0.75), alias → canonical, "Not in our glossary. Ask your clinician or the lab." | `test_search.py` |
| 61 | Passage linking for every card | Values/terms: whole source line; Impression/Remarks/Advice: whole section block; combination card has no passage | `test_passages.py` |
| 62 | Narrative report summary | Header + section skeleton + quoted Impression lines, even with no lab values | `test_summary.py::test_narrative_report_summary` |
| 63 | Short ALL-CAPS abbreviation vs lowercase/unit (`CT` vs `ct`, `Ct`) | Exact-case match only for ≤ 4-char ALL-CAPS aliases | `test_glossary.py::test_ct_is_case_sensitive` |
| 64 | Abbreviation inside a longer token (`PT` in `PTH`, `RA` in `ERA`) | Word boundaries + exact case; PTH matches its own entry | `test_short_caps_not_inside_longer_tokens` |
| 65 | Disease / finding words (cholecystitis, malignant, cardiomegaly) | Defined as words only; fixed note "This explains the word only…"; source line stays NEEDS_PROFESSIONAL | `test_kinds_and_finding_note` |
| 66 | Same alias in two entries (e.g. `TG` triglycerides vs `Tg` thyroglobulin) | Build script and test reject it | `test_no_duplicate_aliases_across_entries` |
| 67 | Glossary wording drift | No entry may contain "you have", "indicates", "means you", "diagnos", "treat"; all pass safety.py | `test_no_forbidden_phrases_in_entries`, `test_every_glossary_entry_passes_safety_filter` |
| 68 | AI quotes a number different from the report | CONTRADICTED "AI says 12, report says 11.2" | `test_verify.py::test_planted_error_is_caught` |
| 69 | AI writes the number in another format (`2,10,000` / `210000` / `2.1 lakh` / `210,000`) | Same value → VERIFIED | `test_number_formats_match` |
| 70 | AI quotes a textbook range instead of the printed one | RANGE_NOT_FROM_REPORT, with both ranges shown | `test_planted_error_is_caught` |
| 71 | AI quotes a range when the report prints none | RANGE_NOT_FROM_REPORT "this range came from elsewhere" | `test_planted_error_is_caught` (vitamin B12) |
| 72 | AI calls a value normal when it is outside the printed range | CONTRADICTED | `test_planted_error_is_caught` (WBC) |
| 73 | AI states a status for an ambiguous (sex/age) or missing range | UNVERIFIABLE "the report doesn't settle this" | `test_status_on_ambiguous_range_is_unverifiable` |
| 74 | AI diagnoses, prescribes/doses, or falsely reassures | OVERSTEPS (diagnosis / treatment / reassurance) | `test_planted_error_is_caught` |
| 75 | AI skips an out-of-range result | Listed as an omission with its report line | `test_omitted_out_of_range_values_are_reported` |
| 76 | AI uses an alias or another name (Hb, SGPT/ALT, WBC vs TLC, PCV) | Matched to the same report row | `test_aliases_hb_and_sgpt` |
| 77 | Two claims in one sentence | Each judged; sentence takes the most severe label | `test_two_claims_in_one_sentence` |
| 78 | Dose numbers ("iron tablets 100 mg") | Never read as a test value; sentence is OVERSTEPS | `test_dose_number_is_not_a_test_value` |
| 79 | Same test printed twice (one row without range) | Status judged against the row whose value the AI quoted | `test_status_judged_against_the_row_whose_value_matched` |
| 80 | AI value in a different unit than the report | UNVERIFIABLE (no unit conversion), never CONTRADICTED | `test_different_unit_is_unverifiable_not_contradicted` |
| 81 | Range written in lakh (`1.5–4.1 lakh`) | Multiplied before comparing; matches `1,50,000 - 4,10,000` | `test_range_in_lakh_matches_report_range` |
| 82 | Paraphrase / number before test name / pronoun / qualitative claim | NEUTRAL (shown, not judged): documented limits | `test_known_limits_are_neutral_not_verified`, `test_paraphrase_without_test_name_is_neutral` |
| 83 | Empty input, oversized input, rate limit | 400 / 422 / 429 (same limits as `/api/analyze`) | `test_api_verify_empty_inputs_400`, `test_api_verify_size_limit`, `test_api_verify_is_rate_limited` |
| 84 | AI says a whole group is normal ("liver enzymes and proteins are all within normal ranges") but one member is outside its printed range | Group claim → CONTRADICTED naming every exception with value and printed range | `test_realworld_replica.py::test_lft_group_claim_contradicted_naming_total_protein`, `test_verify.py::test_group_claim_contradicted_lists_every_exception` |
| 85 | Group claim where every member is in range / some have unsettled ranges | VERIFIED / UNVERIFIABLE | `test_group_claim_verified_when_every_member_is_in_range`, `test_group_claim_unverifiable_when_a_member_has_no_settled_range` |
| 86 | Mixed report panel ("LIVER & KIDNEY PANEL") | Glossary category beats section heading: Creatinine is not a liver test | `test_category_beats_section_heading` |
| 87 | Generic summary under an AI heading ("They are all within normal ranges.") | The tests the AI listed as bullets under that heading define the group | `test_heading_bullets_define_the_group_for_a_generic_summary` |
| 88 | Panel name used as if it were one test ("Your CBC is normal") | Treated as a group, never NOT_IN_REPORT | `test_panel_names_are_groups_not_single_tests` |
| 89 | "indicates / suggests / consistent with <finding>" ("indicates dyslipidemia") | OVERSTEPS with the phrase quoted, never NOT_IN_REPORT | `test_finding_words_after_indicate_suggest_are_oversteps` |
| 90 | Health interpretation ("normal thyroid function", "your liver is healthy") | OVERSTEPS (interpretation) | `test_health_interpretations_are_oversteps` |
| 91 | AI quotes the report's own hedge ("the report's impression says findings suggestive of…") | Not an overstep | `test_quoting_the_report_is_not_an_overstep` |
| 92 | Report test names in lab order ("Cholesterol, Total", "GLUCOSE FASTING (F), PLASMA", "T3, Total", "E.S.R") | Order-independent normalised match with the AI's wording | `test_report_and_ai_test_names_normalise_to_the_same_test` |
| 93 | A heading line ending in "SERUM" above "Bilirubin Total" | Aliases never match across a line break | `test_glossary.py::test_aliases_never_match_across_a_line_break` |
| 94 | Dots in "E.S.R.", "Dr.", "i.e.", "A.", decimals, parentheses | Not sentence ends; "U/L." still is | `test_sentence_splitting_respects_abbreviations_decimals_parentheses` |
| 95 | Interpretation/risk-category tables and pregnancy-trimester ranges under a result | Reference material: not results, still visible as text | `test_end_to_end.py::test_risk_and_trimester_tables_are_not_results`, `test_realworld_replica.py::test_no_result_rows_from_risk_or_trimester_tables` |
| 96 | Real result row right after a bare "Note" heading | Still parsed; the reference section ends there | `test_real_result_after_a_bare_note_heading_is_still_parsed` |
| 97 | Markdown in the AI text (`**mild anemia**`, `*average*`, bullets, headings) | Stripped for extraction (same-length spaces); spans still slice the original | `test_verify.py::test_markdown_is_stripped_for_extraction_but_spans_point_into_the_original` |
| 98 | Status word after a generic phrase ("…the red blood cells, which is normal") in a bullet about one test | Bound to the subject test (MCV), never to "RBC Count" | `test_generic_cells_phrase_is_not_a_status_target_when_a_test_is_the_subject` |
| 99 | Status word in the bullet lead ("**Low Hemoglobin…, Total RBC…, and PCV…**") | Checked for every test in the lead; a wrong lead status is still CONTRADICTED | `test_status_word_in_the_bullet_lead_is_checked_for_every_lead_test` |
| 100 | Status carried over to a later sentence that names no test | Can verify; a mismatch is UNVERIFIABLE, never CONTRADICTED | `test_bullet_subject_carries_over_but_never_contradicts` |
| 101 | Negated / hypothetical status ("hasn't dropped", "no increase", "would be low if", "if it were high") | Not a claim | `test_negated_and_hypothetical_status_words_are_not_claims` |
| 102 | Status word describing the lab's flag (`"H" (High) flag`) or cells (`some small, some normal`) | Not a claim; a plain "(low)" still is | `test_status_words_describing_cells_or_flags_are_not_claims`, `test_a_plain_bracketed_status_is_still_a_claim` |
| 103 | AI says the lab's flag disagrees with the printed range | VERIFIED if the report really has that mismatch, CONTRADICTED if not | `test_flag_mismatch_noted_correctly_is_verified`, `test_flag_mismatch_claimed_but_not_on_report_is_contradicted` |
| 104 | Results linked to a condition or cause ("points to mild anemia", "reacting to a recent mild infection", "recovery from a minor illness") | OVERSTEPS: "This links your results to a condition or cause…" | `test_linking_results_to_a_condition_is_an_overstep`, `test_realworld_chatbot.py` |
| 105 | Definitions ("ESR is a general marker for inflammation") and doctor deferrals ("a doctor would … determine if an iron supplement is necessary") | Not oversteps; a direct "take iron supplements" still is | `test_definitions_and_doctor_deferrals_are_not_oversteps`, `test_direct_supplement_instruction_is_still_an_overstep` |
| 106 | "Normal Differentials" group claim | Group = differential tests only (ESR under the same heading excluded) | `test_realworld_chatbot.py::test_normal_differentials_group_claim_verified` |
| 107 | Threshold, time span or count next to a test ("recommended 200 limit", "3 months", "two values") | Never a value claim | `test_thresholds_time_spans_and_counts_are_never_values` |
| 108 | Attributive "normal" ("normal breakdown of") | Not a status claim | `test_normal_is_a_status_only_when_it_predicates_the_test` |
| 109 | Test named in a comparison inside another test's bullet | Claims bind to the lead test | `test_bold_lead_test_wins_over_a_test_named_in_comparison` |
| 110 | Compound lead "Bilirubin (Total: 1.6, Direct: 0.4, Indirect: 1.2)" | Three tests, each checked and counted as mentioned | `test_compound_lead_names_each_test` |
| 111 | AI announces or uses ranges/categories the report doesn't print | RANGE_NOT_FROM_REPORT | `test_announcing_outside_ranges_is_range_not_from_report`, `test_category_thresholds_bind_to_the_previous_bullet_lead` |
| 112 | Value outside every printed sex/age range on the same side | Status settled and checked; within all / mixed stays UNVERIFIABLE | `test_value_outside_every_printed_range_settles_the_status`, `test_within_every_printed_range_is_not_treated_as_settled` |
| 113 | Condition/state term in a non-exempt sentence | OVERSTEPS by default (lexicon) | `test_condition_or_state_terms_are_oversteps_by_default` |
| 114 | Definitions, test purpose, deferrals, quotes, category thresholds, panel names | Exempt from the default rule | `test_exempt_sentences_are_not_oversteps`, `test_test_purpose_and_condition_word_definitions_are_not_oversteps` |
| 115 | Benchmark overfitting | No gold sentence may appear in the rule code; held-out answers are scored separately, never tuned on | `test_benchmark.py::test_rules_do_not_special_case_benchmark_sentences` |
| 116 | Markdown table in the AI answer | Rows parsed by header words; each row is one claim set; header/separator NEUTRAL; name cell is a label | `test_markdown_table_rows_are_structured_claims`, `test_columns_are_mapped_by_header_words_not_position` |
| 117 | AI applies one of several printed ranges (sex/age/pregnancy unsettled) | UNVERIFIABLE, message names the assumption; quoting one or all ranges is fine | `test_applying_one_sex_range_names_the_assumption`, `test_applying_one_age_band_names_both_bands`, `test_quoting_every_printed_range_is_fine` |
| 118 | AI quotes the report (accurately / with a changed number) | VERIFIED "Quotes your report accurately" / CONTRADICTED "Misquotes your report"; quotes never OVERSTEPS | `test_accurate_quote_is_verified_and_never_an_overstep`, `test_misquote_with_changed_meaning_is_contradicted` |
| 119 | Report hedges a finding; AI states it as certain | OVERSTEPS "Your report says '…'; the AI states it as certain." | `test_dropping_the_reports_hedge_is_an_overstep` |
| 120 | Questions, safety advice, deferrals, negated certainty, definitions | Never claims (NEUTRAL); a definition that links the patient's result still OVERSTEPS | `test_sentence_types_that_are_never_claims`, `test_a_definition_that_links_the_result_to_a_condition_still_oversteps` |
| 121 | "very low TSH and elevated Free T4", glosses, "thyroid hormones", quantifiers, time ranges | Bound to the right test / not claims | `test_adjective_binds_to_the_test_right_after_it` and the other step-5 tests |
| 122 | Reassurance, lifestyle imperatives, organ function, outside cut-off lists | OVERSTEPS / RANGE_NOT_FROM_REPORT (lexicon classes) | `test_recall_classes_are_oversteps`, `test_outside_cutoff_list_and_carried_subject` |
| 123 | Items of a possibility list ("Common causes include:") | No test claims | `test_items_of_a_possibility_list_make_no_test_claims` |
| 124 | Many consecutive interpretation lines | One passage; summary counts passages | `test_consecutive_overstep_bullets_merge_into_one_passage` |
| 125 | Values written in report sentences ("Heart Rate: 88 bpm.", "CTR is about 0.55") | Parsed, no range; an "ECG within normal limits" opinion settles NORMAL | `test_values_written_in_report_sentences_are_parsed_without_a_range`, `test_unranged_value_is_normal_when_the_reports_opinion_says_so` |
| 126 | A general range for a value printed without one; "0.50 or less"; head-noun reference | RANGE_NOT_FROM_REPORT | `test_general_range_for_an_unranged_value_and_or_less_form`, `test_head_noun_refers_back_to_the_bullets_test` |
| 127 | Faithful narrative paraphrase vs added condition / stronger certainty | VERIFIED "Matches your report" vs OVERSTEPS; hedge scope is word-level | `test_faithful_narrative_sentences_match_the_report`, `test_added_condition_or_stronger_certainty_still_oversteps`, `test_word_level_hedge_scope_in_the_report` |
| 128 | Interpretation after an accurate quote | OVERSTEPS (quote stays VERIFIED) | `test_quote_exemption_covers_only_the_quoted_words` |
| 129 | "does not exclude", "is not the same as", Purpose columns, test suggestions, what a test measures | Never claims | `test_cautions_are_not_claims`, `test_purpose_column_of_a_test_table_is_not_a_claim`, `test_suggesting_a_test_and_what_a_test_measures_are_not_claims` |
| 130 | "up to 15" vs "0 - 15"; age-adjusted / guideline thresholds; quoted glosses; enumerated groups; "near the limit" | Value-side bound only; RANGE_NOT_FROM_REPORT; own-test binding; exact members; not LOW/HIGH | the round-2 step 5–6 tests in `tests/test_verify.py` |
| 131 | "no obvious emergency", "functioning normally", "confirms the heart …", "can be associated with:" lists | OVERSTEPS / one passage | `test_reassurance_organ_function_and_certainty_overclaims_overstep`, `test_link_lists_are_one_passage` |
