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
