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
| 32 | Glossary content safety | All 143 entries must pass the filter | `test_every_glossary_entry_passes_safety_filter` |
| 33 | CRLF line endings | Normalised; offsets match the returned text | `test_crlf_input_normalised` |
| 34 | Empty input | HTTP 400 | `test_api.py::test_analyze_rejects_empty` |
| 35 | Text with no medical content | Warning; empty results | `test_empty_and_garbage_input` |
| 36 | Span integrity | `text[start:end] == span.text` for all spans in all samples | `test_all_spans_and_text_safe[*]` |
