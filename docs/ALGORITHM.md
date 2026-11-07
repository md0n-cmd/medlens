# MedLens — Algorithm & Design Decisions

## 1. Pipeline

| Step | Module | Output |
|---|---|---|
| 1. Normalise | `parser/lines.py::normalize_text` | CRLF → LF, tabs → 4 spaces. Offsets are computed on this text, and the API returns it so the UI renders the same string. |
| 2. Segment | `parser/lines.py::segment` | Lines with absolute `start/end`; sections from `KEYWORD:` headers (Findings, Impression, Advice…) or ALL-CAPS title lines. Banner lines (`***`, `===`) end a section. Sections named Impression/Conclusion/Opinion/Advice/Remarks/Comment/Interpretation/Note/… are flagged `interpretive`. |
| 3. Patient sex | `pipeline.py::detect_sex` | Only from an explicit `Sex:` / `Gender:` / `Age/Sex: 34 Y / M` header in the first 25 lines. Otherwise `None`. |
| 4. Extract rows | `parser/values.py` | For every non-interpretive line: test name, value, comparator, unit, lab flag, raw range cell, each with spans. |
| 5. Parse range | `parser/ranges.py::parse_range` | `ReferenceRange` (interval / upper / lower / qualitative / multi / unparseable), built **only from the range cell on that line**. |
| 6. Compare | `ranges.py::compare_*`, `pipeline.py::compute_status` | One of 9 statuses + a fixed-wording explanation. |
| 7. Validate lab flag | `pipeline.py::check_flag` | `flag_disagreement` + note. |
| 8. Terms | `glossary/matcher.py::find_terms` | Glossary matches with spans; each lab row is linked to its glossary term. |
| 9. Unknown terms | `matcher.py::find_unrecognized` | Medical-looking tokens not covered by any known span. |
| 10. Classify | `classify.py` + `pipeline.py` | `ExplanationItem`s tagged FACTUAL / GENERAL / PROFESSIONAL. |
| 11. Safety | `safety.py::sanitize` | Every generated `text` is filtered. Failures are replaced with a neutral fallback and marked `safety_filtered`. |

## 2. Why rule-based instead of an LLM

* **No hallucinated diagnoses.** The core requirement is to *avoid unsupported diagnoses*. A deterministic system can only output sentences we wrote and reviewed.
* **Reference ranges must come from the report.** An LLM tends to "helpfully" fill in population ranges it has memorised. Our parser has no ranges to fall back on.
* **Exact provenance.** Every output carries character offsets into the input, which the UI uses for two-way linking. Generative output does not give that reliably.
* **Testable.** 494 unit and end-to-end tests pin the behaviour, and the same input always gives the same output.
* **Offline, free, fast.** No API key is needed, and a report takes a few milliseconds.
* An LLM is used only as an **optional, clearly labelled fallback** for glossary misses. It is off by default and goes through the same safety filter.

## 3. Row extraction

1. **Columnar layout:** split on 2+ spaces, `|` or tabs. Column 0 is the name, which must contain a letter and must not be metadata (`Age`, `Sex`, `Date`, `Ref. By`…). **Column 1 must be a value**; otherwise the line is not a result row. This rejects headers and narrative text.
2. A value cell is `[<|<=|>|>=|≤|≥]? NUM ([-–] NUM)? FLAG? UNIT? FLAG?`, or a qualitative word from a fixed vocabulary.
3. After the value: an optional stand-alone flag column, then the **longest run of columns that parses as a range** (so `M: 0.7-1.3   F: 0.6-1.1` stays one cell). Columns between the value and the range are the unit. If a trailing column exists but cannot be parsed, it is kept as the range so the status is `unparseable` rather than silently "no range".
4. **Inline fallback:** `Name: value unit (range)`, `Name value unit Ref: range`, `Name value unit [range]`.
5. Units are strictly validated: a "unit" containing a decimal number (e.g. `x 4.5 cm`) disqualifies the line. This keeps radiology measurements out of the lab table.

## 4. Reference-range grammar

```
range      := wrapper? prefix? (multi | single)
wrapper    := "(" range ")" | "[" range "]"
prefix     := ("Ref" | "Reference" | "Bio. Ref. Interval" | "Normal range" | "Range") [:-=]?
multi      := (LABEL [:-=]? single [;,/|]?)+       LABEL ∈ {M, F, Male, Female, Men, Women, Adult, Child, …}
single     := interval | upper | lower | and_above | qualitative
interval   := NUM DASH NUM UNIT?                   DASH ∈ {-, –, —, to, ~}; lo must be ≤ hi
upper      := UOP NUM UNIT?                        UOP ∈ {<, less than, below, under} (exclusive)
                                                         {<=, ≤, up to, upto, max} (inclusive)
lower      := LOP NUM UNIT?                        LOP ∈ {>, more than, greater than, above, over} (exclusive)
                                                         {>=, ≥, min, at least} (inclusive)
and_above  := NUM ("and above" | "or more" | "+") UNIT?    (inclusive)
qualitative:= Negative | Positive | Non-reactive | Absent | Nil | Trace | Clear | Pale yellow | …
NUM        := \d{1,3}(,\d{2,3})+ (\.\d+)?  |  \d+(\.\d+)?  |  \.\d+     (Indian & Western grouping)
UNIT       := (x10^n)? [letters µ % / ^ digits . -]+ (one extra word allowed, e.g. "mm/1st hr")
```

## 5. Comparison semantics

Every value and every numeric range becomes a real **interval** with open/closed ends:

| Input | Interval |
|---|---|
| value `11.2` | `[11.2, 11.2]` |
| value `<0.01` | `(-∞, 0.01)` |
| value `>=200` | `[200, ∞)` |
| value `6-8` (e.g. pus cells /hpf) | `[6, 8]` |
| range `13-17`, `Up to 40`, `≤ 5`, `≥ 60` | closed ends |
| range `<200`, `>40`, `Less than 5`, `More than 40` | open ends |

* **within_range** if value ⊆ range.
* **above_range** if every point of the value lies above the range's upper end. For example, `200` vs `<200` is above, because 200 is not < 200.
* **below_range** is the mirror case.
* Otherwise **indeterminate**, e.g. `<5` vs `0.27-4.2`: the true value could be inside or below.

**Boundary decision:** a value equal to an inclusive bound is *within*, and a value equal to an exclusive bound is *out*. Ranges printed as `a-b` are read as inclusive, because that is the convention on Indian and Western lab reports.

Other statuses:
* No range cell → `no_reference_range_supplied`. Never flagged. We have no built-in ranges.
* Both value and range state a unit, and they differ after cosmetic normalisation → `unit_mismatch`. No conversion is attempted.
* `multi` range → `ambiguous_range`, *unless* the header states the sex and exactly one alternative matches. In that case it is used, with a note saying so. A single non-sex label (e.g. `Adult: …`) is used with a note.
* Qualitative values: same normalised word, or same equivalence group (`Nil`≡`Absent`≡`Negative`), → within. Different known groups (`Trace` vs `Negative`) → `differs_from_reference`. Unknown vocabulary → `indeterminate`.
* Number vs. word mismatch → `indeterminate`.

**Lab-flag validation:** `H/HH/High/↑` expect *above*, `L/LL/Low/↓` expect *below*, and `*`/`A`/`Abnormal`/`Critical` expect *any out-of-range*. A conflict sets `flag_disagreement` and adds a NEEDS_PROFESSIONAL card. A value outside the printed range with no lab flag gets a factual note.

## 6. Term matching

* All aliases go into one regex alternation sorted by length (longest first), wrapped in `(?<![A-Za-z0-9]) … (?![A-Za-z0-9])`. So "Direct Bilirubin" beats "Bilirubin", and "ALT" never matches inside "SALT" or "Hb" inside "HbA1c".
* Inside multi-word aliases, separators are flexible (`Bilirubin, Direct` = `Bilirubin (Direct)` = `Bilirubin Direct`).
* Aliases of 3 characters or fewer are case-sensitive (as written, or ALL CAPS) so that `hb` or `pt` in prose is not matched. Longer aliases are case-insensitive.
* Ambiguous abbreviations (`PCV`) carry `possibilities` with context keywords. If exactly one meaning's keywords occur in the report, that meaning is used. Otherwise all meanings are listed.
* **Unrecognized terms:** 2–5 char ALL-CAPS tokens and `-itis/-emia/-osis/-iasis/-megaly/-ectomy/-opathy/-plasia/-algia/-uria/-penia` words that are not covered by a glossary, value, unit or range span. A stopword list and ALL-CAPS title lines are excluded. They are listed with *"ask your clinician"* and never given a meaning.

## 7. Classification rules

| Tag | Produced for |
|---|---|
| FACTUAL_FROM_REPORT | One per lab row: value, unit, printed range, lab flag, mechanical comparison result |
| GENERAL_INFO | One per distinct glossary term: what it measures or means in general |
| NEEDS_PROFESSIONAL_INTERPRETATION | out-of-range / differs / ambiguous rows; flag disagreements; ≥2 abnormal values (combination); every line in an interpretive section; hedged sentences elsewhere; unrecognized terms |

Hedge lexicon: *suggestive of, suggests, consistent with, compatible with, cannot be ruled out / excluded, to rule out, r/o, s/o, likely, unlikely, probable/probably, possible/possibly, may be/represent/indicate, could be, query, `?term`, correlate clinically, clinical correlation, favours, differential diagnosis, advised, recommended, follow-up, further evaluation*.

## 8. Provenance and linking

* Every `LabValue` carries `test_span`, `value_span`, `unit_span`, `flag_span` and `reference_span`. Every `TermMatch`, `UnrecognizedTerm` and `ExplanationItem` carries a `span` and a `source_snippet` (the full source line).
* Tests check `text[start:end] == span.text` for every span in every sample.
* The UI cuts the report at every span boundary and wraps each segment in a `<span>` listing all covering classes. A click resolves to the *innermost* covering span's explanation card. Clicking a card flashes every segment inside the card's span and scrolls it into view.

## 9. MedLens Verify: checking AI explanations

Input: the report and an AI-written explanation of it. The report goes through the normal pipeline. The AI text goes through `app/verify/claims.py` (extraction), then `app/verify/check.py` (checking). No LLM is involved.

### 9.1 Claim extraction
0. **Markdown.** `**bold**`, `*italic*`, `_x_`, `` `code` ``, bullet markers and `#` headings are replaced by spaces of the same length before extraction. The offset map is therefore the identity, and every span is re-sliced from the original text. Headings, bullets and **bold** bullet leads are read from the original markup.
1. **Sentences.** Split on `.`/`!`/`?` followed by whitespace, and on newlines. A `.` inside a number (`11.2`), after a dotted abbreviation (E.S.R., i.e.), after Dr./Mrs./No., after a standalone capital, or inside parentheses never ends a sentence. Character offsets are kept.
2. **Test mentions.** The glossary matcher (same longest-match and case rules), keeping entries of kind `test` plus ambiguous abbreviations such as PCV.
3. **Segments.** The claims for a mention are read from the text after it, up to the next mention. `"Your MCV and MCH are normal"`: a mention followed only by a conjunction shares the next mention's status.
4. In each segment, ranges are found first, so their numbers are never read as values:
   * **Intervals** `a–b`, `a to b`, `between a and b`. These count as a range only when a cue word (range, normal, reference, should, between, …) appears earlier in the sentence, or the interval is in brackets. Otherwise `6-8 /hpf` is read as a value written as a span.
   * **Upper / lower bounds**: `below / under / less than / up to / < / ≤ N`, `above / over / more than / at least / > / ≥ N`.
   * `lakh` / `thousand` / `k` multiply the numbers (`1.5–4.1 lakh` = 150000–410000).
5. **Value.** The first remaining number, with comparator, multiplier (`2.1 lakh`) and unit. A number followed by a dose unit (`100 mg`, `2 tablets`) is a dose, never a test value.
6. **Subject binding.** A sentence's subject is the set of tests in its bullet's **bold lead** ("**Normal MCV (89.0 fL):**", carried over to later sentences of the same bullet), or else the tests that have a value in the sentence. Generic phrases ("red blood cells", "white blood cells", "leukocytes") are never status targets when a test is the subject: their status word moves to the subject, or is dropped if there are several subjects. A status word in the lead itself ("Low Hemoglobin…, Total RBC…, and PCV…") is checked for every test in the lead. A status *carried over* to a sentence that names no test can verify, but a mismatch is only UNVERIFIABLE, never CONTRADICTED.
7. **Status words.** LOW (low, below the range, decreased, reduced, deficient), HIGH (high, above the range, elevated, raised, increased), NORMAL (normal, within/in the range, fine, healthy, ok). "normal range is …" is not a status. A negation in the three words before (`not`, `isn't`, `hasn't`, `no`, `never`, `without`) drops the claim, and so does a hypothetical clause (`if`, `would`, `could`, `might`, `were`). Also not claims: a status word in quotes or brackets next to the word *flag* (`"H" (High) flag`), and one describing cells, variation or ability (`some small, some normal`, `normal average size`, `normal blood clotting ability`). "deficient/deficiency" is not a LOW status, because it's interpretation, not a reading. A sentence saying the lab's flag disagrees with the printed range ("has an H flag … but … within the range") becomes a **flag claim**.
8. **Oversteps**, per sentence. Everything `safety.py` blocks, plus extra rules: `you are suffering/anaemic/diabetic…`; `this/it/which indicates|confirms|suggests|means|shows|points to … <disease word>` (anaemia, diabetes, infection, disease, deficiency, cancer, failure, hypo/hyperthyroidism, *-itis/-emia/-osis/-pathy*); dosage (`N mg/mcg/IU/tablets`, `twice a day`, `every morning`); "should take … tablets/supplements"; false reassurance (`no need to see a doctor / to worry`, `nothing to worry about`, `don't worry`, `no cause for concern`, `you're fine/healthy`). Each is categorised as diagnosis, treatment, reassurance, interpretation (a statement about your health) or **link**. A *link* is an interpretive verb (points to, indicates (that), suggests, is (often) an early sign of, usually means, consistent with, typical of, commonly occur together during, due to, caused by, a result of, reacting to, recovery from, shows … patterns, aligns with) followed within the clause by a condition: a glossary *finding*, an -emia/-itis/-osis/-cytosis/-penia/-pathy word, or one of anemia, infection, inflammation, immune response, deficiency, illness, disease, disorder, stress, dehydration, insufficiency, failure, damage. Its message reads "This links your results to a condition or cause ('…'). That is an interpretation only your doctor can make." Not flagged: definitions ("ESR is a general marker for inflammation") and doctor deferrals ("a doctor would review … to determine if an iron supplement is necessary"). A direct "take iron supplements" still is.

### 9.2 Checking rules
Report rows are indexed by every glossary term inside their test name. A few related names are also accepted: WBC ↔ TLC, PCV ↔ Packed Cell Volume, Bilirubin → Total Bilirubin, Glucose → FBS/RBS/PPBS, PT → INR.

| Claim | VERIFIED when… | Otherwise |
|---|---|---|
| Value | the number equals a report value for that test (relative tolerance 1e-6, so `2,10,000` = `210000` = `2.1 lakh`; span values must match both ends) | **CONTRADICTED** "AI says X, report says Y". If both texts give units and they differ: **UNVERIFIABLE** (no unit conversion, ever). Test not on the report: **NOT_IN_REPORT**. |
| Range | same kind and the same bounds as the printed range (or any labelled alternative) | **RANGE_NOT_FROM_REPORT** "AI quotes …; your report prints …", or "your report prints no range for this test; this range came from elsewhere" |
| Status | equals MedLens's computed status (ABOVE→HIGH, BELOW→LOW, WITHIN→NORMAL), judged against the row whose value matched in the same sentence when there is one | **CONTRADICTED**. Ambiguous, missing or unreadable range, or several rows that disagree: **UNVERIFIABLE** "the report doesn't settle this" |
| Flag ("has an H flag but … within the range") | the report row really has a lab flag that disagrees with its printed range: "The AI correctly noted the lab's flag doesn't match the printed range." | **CONTRADICTED** (flag and range agree on the report) |
| Overstep | – | always **OVERSTEPS** "This goes beyond what a report explanation should say; ask your doctor." |

| Group ("liver enzymes and proteins are all within normal ranges", "blood counts are healthy", "all results are normal") | every report row in the group is within its printed range | **CONTRADICTED** naming every exception ("Total Protein (6.33) is below the range printed on your report (6.40–8.30)"); unsettled members → **UNVERIFIABLE**; no rows → **NOT_IN_REPORT** |

**Group membership** combines three sources: report section headings (`/liver|lft|hepatic/`, `/blood count|cbc|hemogram|differential/`, …), the glossary category of the row's test, and the tests the AI itself listed as bullets under its own heading ("**Liver Function Tests (LFT)**" followed by bullets). The category wins over the section: Creatinine under "LIVER & KIDNEY PANEL" is not a liver test. ESR is a blood count only under a CBC heading. Panel names (CBC, lipid profile, LFT) are treated as groups, never as single tests. A group phrase inside a specific test name ("fasting blood sugar") is not a group.

* **Sentence label.** The most severe finding wins: CONTRADICTED > OVERSTEPS > RANGE_NOT_FROM_REPORT > NOT_IN_REPORT > UNVERIFIABLE > VERIFIED > NEUTRAL.
* **Omissions.** Every report value outside its printed range whose test (or a related name) is never mentioned anywhere in the AI text.
* **Provenance.** Every finding carries `ai_span` (inside its sentence) and `report_span` (the whole report line). Tests check both slice their source texts exactly. MedLens's own messages pass `safety.py`; a test caught an early message that used the word "dosage".

### 9.3 Known limits (measured, see `tests/test_verify.py`)
* **Paraphrases aren't extracted.** "Your blood count is a bit off" names no test and has no number, so it comes out **NEUTRAL**: shown, never counted as verified.
* **A number before the test name** ("12 g/dL of hemoglobin was found") isn't attached to the test, so it's NEUTRAL.
* **Pronouns across sentences** ("Your hemoglobin is low. It is 11.2.") aren't resolved: the second sentence is NEUTRAL.
* **Qualitative claims** ("Your HBsAg is negative") aren't checked yet: NEUTRAL.
* **No unit conversion.** "5.8 mmol/L" against a report value of "104 mg/dL" is UNVERIFIABLE, never CONTRADICTED.
* **Status words are lexical.** Unusual wording ("a touch under par") isn't recognised. Negation is handled only within three words.
* **Overstep detection is pattern-based.** Novel phrasing of a diagnosis or advice can slip through. Every pattern errs towards flagging. A sentence that explicitly quotes the report ("the report's impression says…") is exempt from the finding-word rule.
* **Group claims cover "normal" only.** "Your lipids are all high" is not checked as a group.
* **Precision over recall for CONTRADICTED.** Indirect references (a status carried over from a bullet lead) and unclear bindings are UNVERIFIABLE or dropped, never CONTRADICTED. Some genuine errors phrased indirectly will therefore go unreported.

### 9.4 Real-world test findings

MedLens Verify was run on a **real** multi-page lab report from a large Indian diagnostic chain, together with an explanation of it written by a general-purpose AI chatbot. The output was compared by hand with the report. **For privacy, neither real text is in this repository.** Both were replaced by synthetic replicas with a fictional patient, doctor and lab, changed values, and the same layout and chatbot style: `samples/10_lipid_thyroid_lft_cbc.txt` and `samples/ai/10__realistic.txt`. `tests/test_realworld_replica.py` pins every fix below.

| What failed on the real texts | Cause | What changed |
|---|---|---|
| The chatbot's most important error went **unflagged**: "Liver enzymes and proteins are all within normal ranges" while Total Protein was below its printed range. "Blood counts are healthy" while RDW-CV was above. | Only single-test claims were extracted. | **Group claims** (§9.2): the group is mapped to report rows and every exception is named. |
| "indicates dyslipidemia" came out **NOT_IN_REPORT** instead of OVERSTEPS, and "normal thyroid function" wasn't flagged. | The diagnosis pattern required a particular subject ("this/these…"). Panel names ("lipid profile") were treated as missing single tests. Health interpretations weren't covered. | Subject-free "indicates/suggests/confirms/consistent with/points to <finding>", where a finding is a glossary entry of kind *finding* or an *-emia/-itis/-osis/-pathy/-megaly/-lipidemia* word. New *interpretation* rules ("normal thyroid function", "your liver is healthy"). Panels are groups. Messages quote the phrase and say why. |
| "Total Cholesterol: 212" → NOT_IN_REPORT, **and** Cholesterol, Total listed as omitted. "Bilirubin Total" → NOT_IN_REPORT. | A heading ending in "…, SERUM" plus the next line's "Bilirubin Total" matched the alias "Serum Bilirubin" **across the line break**, hiding the real test name. This bug also affected the main analyzer. | Alias separators no longer cross a newline. An order-independent normalised-name fallback ("Cholesterol, Total" = "Total Cholesterol", "GLUCOSE FASTING (F), PLASMA" = "Fasting Glucose", "E.S.R" = "ESR"). New `E.S.R` aliases. |
| "E.S.R. (Erythrocyte Sedimentation Rate): 19 mm/hr" was split after "E.S.R." and the value lost its test. | The splitter broke on any `.` followed by a space. | Splitting ignores dotted abbreviations, Dr./Mrs./No./i.e./e.g., standalone single capitals, decimals and parentheses. "U/L." still ends a sentence. |
| Rows like "Optimal < 200", "Very high risk group LDL-C < 70" and "1st Trimester 0.100 - 2.500" became **result rows**. CBC rows fell into the thyroid section. | Interpretation tables look like result rows. The heading "COMPLETE BLOOD COUNT;CBC" (with `;`) wasn't recognised. | Reference rows (risk/trimester labels, or a value that is itself a range/comparator with no unit) are skipped but stay visible as plain text. Bare "Interpretation / Risk Category / Note / Comments" headings start reference sections that end at the next real result row. Headings may contain `;`. |

On the replica, before → after (measured with the code before and after these changes): NOT_IN_REPORT findings 8 → 0, false result rows 12 → 0, group claims checked 0 → 3 (2 CONTRADICTED, 1 VERIFIED), overstep sentences 1 → 3, omissions 3 (two false) → 1 (true: Non-HDL Cholesterol). One honest caveat: the replica's "These results indicate dyslipidemia" was already flagged before the change, because its subject happened to match the old pattern. The NOT_IN_REPORT failure came from the real chatbot's different wording ("Your lipid profile indicates…"), which is now covered by `test_finding_words_after_indicate_suggest_are_oversteps`.

### 9.5 Real-world test 2

A real general-purpose chatbot was asked to explain **synthetic** sample 01. Its answer is committed verbatim as `samples/ai/01_cbc__chatbot_real.txt` ("Real chatbot answer" in the dropdown); it is about synthetic data, so committing it is fine. The numbers in the answer were all correct. Its problem was interpretation: it read the CBC as "mild anemia" and a "mild immune response". Every expected outcome is pinned in `tests/test_realworld_chatbot.py`.

| Finding | Cause | Fix |
|---|---|---|
| 3 **false CONTRADICTED** (all of them) | (a) "**Normal MCV (89.0 fL):** …the average size of the red blood cells, which is normal" bound *normal* to "red blood cells", which the glossary maps to the RBC Count test (below range). (b) "…an early sign of a nutritional deficiency" read *deficiency* as a LOW status for RDW. (c) `"H" (High) flag` read the lab's flag as the AI claiming the platelets are high. | Subject binding (bold lead, value-bearing test; generic cell phrases never targets). *Deficiency* is no longer a status word. Status words that are negated, hypothetical, in quotes/brackets next to *flag*, or describe cells or ability are not claims. A dedicated flag claim checks "has an H flag but is within the range" against the report's real flag mismatch: VERIFIED here. |
| Only 1 of 6 interpretations flagged | "points to **mild anemia**" was hidden by markdown. "usually indicates that the body is reacting to a recent mild infection", "commonly occur together during recovery from a minor illness", "shows two main patterns: mild anemia…" and "normal blood clotting ability" matched no rule. | Markdown stripped with an identity offset map. A *link* overstep (interpretive verb + condition in the same clause). "normal <X> ability/function/health" for any X. Definitions and doctor deferrals are exempt. |
| Bullet leads ignored | "**Low Hemoglobin (11.2 g/dL), Total RBC (4.10), and PCV (36.5%):**" carries a status claim before the test names. | The lead's status word is checked for every test in the lead: 3 × VERIFIED. |
| "Normal Differentials" checked only test by test | No group for differential counts. | A `dlc` group whose members are the differential tests themselves. ESR, printed under the same heading in sample 01, is not counted. |

Result on the chatbot answer, before → after: **CONTRADICTED 3 → 0**, OVERSTEPS 1 → 6, VERIFIED 13 → 26, omissions 1 → 1 (MCHC, correct). All six other AI samples kept exactly the same counts through every change in this round. The Verify summary bar now leads with "The AI made 6 interpretations only a doctor can make."

### 9.6 Benchmark-driven rules

**The benchmark.** Real chatbot answers about *synthetic* samples (`samples/ai/*__chatbot_real.txt`) with hand-checked labels in `tests/benchmark/gold.json`: 58 sentence cases plus 2 omission checks. `python scripts/benchmark.py` prints the per-case table. `tests/test_benchmark.py` fails on **any** false alarm, or if `correct` drops below the best score in `tests/benchmark/score.json` (a ratchet). A *false alarm* is MedLens accusing the AI wrongly: a CONTRADICTED, RANGE_NOT_FROM_REPORT, NOT_IN_REPORT or OVERSTEPS label the gold set doesn't accept, or a forbidden omission.

**Score history**

| Step | Correct | False alarms | Misses | What changed |
|---|---|---|---|---|
| baseline | 34/60 | 7 | 19 | The build before these rules. The user's harness reported 33/60 with 8 false alarms; one case is scored differently because this harness also finds a case's sentence after stripping markdown. |
| 1 | 42/60 | **0** | 18 | Numbers that aren't values; predicative "normal"; lead beats comparison; compound leads; suggested tests; reference population |
| 2 | 47/60 | 0 | 13 | Outside-range announcements; category thresholds; settled multi-ranges; bold-value anchors |
| 3 | 60/60 | 0 | 0 | Interpretation by default, from a data lexicon |
| 5a | 60/60 | 0 | 0 | Precision fixes found by probing sentences *outside* the benchmark |

**Every rule, with a general example** (none of the examples is a benchmark sentence; `test_rules_do_not_special_case_benchmark_sentences` fails if a gold sentence appears in the rule code)

| Rule | Example → result |
|---|---|
| 1a. Thresholds, time spans and counts are never VALUE claims | "keep it under the recommended 150 limit", "over the past 6 weeks", "two readings" → no value claim. "90 or higher" is a range claim. |
| 1b. "normal" is a status only when it predicates the test | "Your TSH is normal", "within normal limits" → status. "the normal turnover of cells", "a normal part of" → no claim. |
| 1c. A test named only in a comparison is not the subject | "**Note on ALP:** Like ALT, this value is within range (40–129)" → the range binds to ALP. |
| 1d. Compound leads name one test per label | "Cholesterol (HDL: 45, LDL: 130)" → two tests, each checked, each counted as mentioned, and the group for "Both are…". |
| 1e. Suggested tests and doctor deferrals make no test claims | "your doctor may order a TSH test" → NEUTRAL. |
| 1f. A reference population is not a judgement | "a healthy adult usually has 90 or higher" → range check, not OVERSTEPS. |
| 2a. Announcing outside ranges | "based on typical lab cut-offs", "standard ranges usually say…" → RANGE_NOT_FROM_REPORT. |
| 2b. Category thresholds | "Borderline high is 200 to 239" → RANGE_NOT_FROM_REPORT unless the report prints that category. "Normal is under 200" is checked against the printed range. The subject carries over from the previous bullet lead. |
| 2c. Several printed ranges | A value outside **all** of them on the same side is settled and checked. Within all of them, or mixed, is UNVERIFIABLE. |
| 2d. Bold-value anchor | "The **72 U/L** value is fine" → the one report row with value 72; no printed range → UNVERIFIABLE. |
| 3. Interpretation by default | A sentence with a CONDITION/STATE term that is not EXEMPT → OVERSTEPS: "This links your results to a condition, cause or health judgement ('…'), which only a doctor can interpret." |
| 3-exempt | Definitions ("CRP is a protein made…", "the test measures…", "used to screen for…", "Anemia means…"), doctor deferrals ("a doctor would…", "ask your doctor"), quoting the report, defining a category threshold. |
| 3-not-a-condition | Panel or test names ("diabetes panel", "inflammation marker"), what something protects against ("fights infection"), words inside a test name, causes about the data ("due to a typo"). |

**The lexicon approach.** Instead of hunting for interpretive *phrases*, `app/verify/lexicon.py` lists condition and state *terms* as data:
- glossary findings and condition suffixes;
- diseases and categories;
- organ states;
- health judgements;
- causes and prognosis;
- lifestyle and treatment advice;
- plus the exemptions.

The rule in `claims.py` stays the same as the lists grow. One judgement call differs from the original list: "well-balanced" counts as a judgement only when said about the person or the body. The gold set expects "Calcium and Phosphorus are well-balanced and normal" to be UNVERIFIABLE, and a statement about test values isn't a statement about the person.

**Known limits**
* **60/60 measures fit, not generalisation.** The rules were written while looking at these answers. The overfitting guards (no benchmark sentence in the rule code; general examples; probing sentences outside the set, which found three precision bugs fixed in 5a) reduce the risk but don't measure it. The held-out answers will: they are scored separately with `scripts/benchmark.py --heldout` and never tuned on (`tests/benchmark/README.md`).
* **Recall is bounded by the lexicon.** Interpretation without a listed term is missed ("your results look mostly typical", "drink plenty of water before the next test").
* **Sentence-level exemption.** A definition exempts its whole sentence, so "X is a protein; yours suggests liver disease" in one sentence is missed. A condition word in a sentence that isn't exempt is flagged even when the AI only mentions it in passing.
* **The benchmark is small:** 4 answers from one chatbot, about 4 synthetic reports.

### 9.7 Held-out round 1

**What it was.** Three real chatbot answers about synthetic samples 04, 05 and 06, written in a different "worried patient" style: markdown tables, symptom and cause lists, follow-up questions, quoted report text. They were hand-labelled by the user (`tests/benchmark/heldout.json`, `samples/ai/heldout/`). They were scored **once on the unchanged build**, and that number was committed before any change (commit `aa1567d`, logged in `tests/benchmark/heldout_score.json`).

| Step | Held-out | False alarms | Misses | Main set | What changed |
|---|---|---|---|---|---|
| first run, unchanged build | **26/63** | **18** | 19 | 60/60, 0 FA | the honest generalisation number for the rules up to §9.6 |
| 1 | 30/63 | 16 | 17 | 60/60, 0 FA | markdown tables as structured rows |
| 2 | 31/63 | 16 | 16 | 60/60, 0 FA | multi-range tests: claims relying on one range |
| 3 | 36/63 | 11 | 16 | 60/60, 0 FA | quotes and certainty |
| 4 | 41/63 | 5 | 17 | 60/60, 0 FA | sentence types that are never claims |
| 5 | 45/63 | **0** | 18 | 60/60, 0 FA | binding fixes |
| 6 | **63/63** | 0 | 0 | 60/60, 0 FA | recall by class (lexicon) |
| 7 | 63/63 | 0 | 0 | 60/60, 0 FA | noise control (passages) |

(Step 1's commit message shows "before 29/63", a mid-step snapshot; the true pre-step score was 26/63, as corrected in step 2's message.)

**The rules, each with a non-benchmark example.** These are structural rules, not phrases from the answers; a test fails if any benchmark sentence appears in the rule code.

| Rule | Example → result |
|---|---|
| Markdown tables | Columns are mapped by header words (test/parameter/investigation; result/value; reference/range/normal; interpretation/finding/assessment), not position. "\| Interpretation \| Reference \| Parameter \| Value \|" works. Each row is one claim set; header and separator rows are NEUTRAL; a row's name cell is a label, not interpretation. |
| Multi-range assumption | Report prints "Men >40 \| Women >50", no sex; AI: "Your HDL of 45 is acceptable for men (above 40)" → UNVERIFIABLE: "The AI used the Men range (> 40). Your report doesn't state sex; for women it prints > 50." Quoting one range without applying it, or quoting all of them, is fine. |
| Quotes | "The report says 'A 9 mm focus …'" when the report says 6 mm → CONTRADICTED "Misquotes your report". An exact quote → VERIFIED "Quotes your report accurately"; never OVERSTEPS. |
| Certainty | Report: "likely a calculus"; AI: "There is a stone in your gallbladder" → OVERSTEPS "Your report says 'likely a calculus'; the AI states it as certain." Synonym families: `FINDING_SYNONYMS` in the lexicon. |
| Never claims | Questions ("Do you eat much fried food?"), doctor deferrals, safety advice ("Do not stop your tablets without asking your doctor", "seek urgent care if …"), negated certainty ("does not confirm hepatitis"), definitions whose subject is a medical term ("Anaemia means …"). Headings keep status checks but never OVERSTEPS. |
| Binding | "very low TSH and elevated Free T4": each adjective binds to the next test. "LDL (bad cholesterol)": a gloss, not a test. "thyroid hormones" = T3/T4 only. "Most/many/some …" is never an all-claim. "after 2–3 months" is never a value. |
| Reassurance | "Good news: this is easily manageable." → OVERSTEPS |
| Lifestyle imperatives | "Cut down on salty snacks." → OVERSTEPS (an imperative verb plus a lifestyle object; a deferral like "your doctor may suggest you reduce sugar" is not) |
| Organ function | "Your liver may be producing less albumin than usual." → OVERSTEPS |
| Outside cut-off lists | Under a "Vitamin B12" heading: "- Under 12: Deficient." → RANGE_NOT_FROM_REPORT unless the report prints that table. Then "Your result is on the low side." binds to the topic test → UNVERIFIABLE when the report prints no range. |
| Possibility lists | Items under "Common causes include:" or "You might experience:" make no test claims (they still count as interpretation). |
| Noise control | Consecutive interpretation lines in one section (a list, or prose separated only by framing lines) are one *passage*. The summary bar counts passages. Worst answer: 28 flagged sentences → 13 passages. |

**This set is no longer held-out.** The rules above were tuned while looking at these answers, so 63/63 measures fit, not generalisation. Both sets are now training data, ratcheted in `tests/test_benchmark.py` (`score.json` → `sets.heldout_round1`). **A new held-out set, scored once on arrival before any change, is required for the next honest number.** The protocol is in `tests/benchmark/README.md`.

### 9.8 Held-out round 2

**What it was.** Five real chatbot answers from two chatbots ("worried", "plain" and "my father" prompts) about synthetic samples 08 (coagulation/cardiac), 09 (chest X-ray + ECG, the first narrative report in a benchmark) and 10 (lipid/thyroid/LFT/CBC). Hand-labelled by the user (`tests/benchmark/heldout2.json`, `samples/ai/heldout2/`). Scored **once on the unchanged build** `4c71b5e`, and that number was committed first (`483d291`, `tests/benchmark/heldout2_score.json`). The benchmark now reports **labs vs imaging** separately (a report with FINDINGS / IMPRESSION / Opinion sections is imaging).

| Step | Round 2 | False alarms | Misses | Labs | Imaging | Main / round 1 |
|---|---|---|---|---|---|---|
| first run, unchanged build | **38/73** | **21** | 14 | 28/43 | **10/30** | 60/60, 63/63, 0 FA |
| 1 narrative report values | 44/73 | 17 | 12 | 28/43 | 16/30 | unchanged |
| 2 narrative findings "match your report" | 52/73 | 9 | 12 | 29/43 | 23/30 | unchanged |
| 3 quote exemption = quoted words only | 53/73 | 9 | 11 | 29/43 | 24/30 | unchanged |
| 4 never-claims | 58/73 | 5 | 10 | 31/43 | 27/30 | unchanged |
| 5–6 ranges and binding | 64/73 | **0** | 9 | 37/43 | 27/30 | unchanged |
| 7 recall | **73/73** | 0 | 0 | 43/43 | 30/30 | unchanged |

The imaging score on first contact (10/30) was the weak spot: the report parser read no values from sentences like "Heart Rate: 88 bpm." and Verify had no notion of a narrative finding, so faithful paraphrases of the report ("The windpipe is centered") were OVERSTEPS through the lexicon.

**The rules, each with a non-benchmark example.**

| Rule | Example → result |
|---|---|
| Narrative values (`app/parser/narrative.py`) | Report prose "QRS duration 96 ms. Cardiothoracic ratio is about 0.48" → two values with units and **no range**. Only glossary *test* names count. |
| Report opinion settles NORMAL | A value without a range in a study section followed by an opinion "ECG within normal limits" (no except/but/borderline): AI "Heart rate 72 – normal" → VERIFIED with the opinion line as evidence. HIGH/LOW stay unverifiable. |
| General ranges for unranged values | "A normal resting heart rate is 60–100" or "CTR is generally 0.50 or less" → RANGE_NOT_FROM_REPORT (never NOT_IN_REPORT). "X or less / or lower / or below" is an upper range. A head noun ("A standard ratio is under 0.50") refers back to the bullet's test. |
| Findings with word-level hedges (`app/verify/findings.py`) | Report "Left lower zone opacity, likely infiltrate": opacity stated, infiltrate hedged. "Cardiomegaly." unhedged. Each mention has polarity: finding present/absent; structure normal/abnormal. Plain-language names are data (`LAY_FINDINGS`: windpipe, air leak, hazy patch, enlarged heart …). |
| Matches your report | Every finding in the AI sentence matches the report, same polarity (absent ≡ normal), same or weaker certainty → VERIFIED "Matches your report: <line>". Overrides lexicon OVERSTEPS that only name or gloss those findings. The sentence with the findings blanked out is re-checked: an added cause, condition or prognosis ("points to pneumonia") still OVERSTEPS. Not for questions, deferrals, safety advice, headings, definitions. Certainty OVERSTEPS fires only when the report states the finding *only* with a hedge. |
| Quote scope | 'The report says "possible consolidation", which usually means pneumonia.' → the quote is VERIFIED, the text after it is checked → OVERSTEPS. Without quote marks, attributing words to the report stays exempt. |
| Never-claims | "A normal X-ray does not exclude …", "A high value is not the same as a diagnosis", a test table's Purpose / Why it is useful / Reason / Use column, "an ultrasound can help assess …", "The ECG records the heart's electrical activity". |
| Value-side bound | Report "0 - 15", value 19: AI "normal is up to 15" → VERIFIED. Report "11.60 - 14.00", value 14.3: AI "up to 14.0" → VERIFIED. Only the bound on the value's side is compared. |
| Outside thresholds | "Some guidelines use a lower LDL cut-off for older adults", "an age-adjusted threshold" → RANGE_NOT_FROM_REPORT. |
| Binding | 'Total Cholesterol (212), Triglycerides (168), and LDL "bad" Cholesterol (137.40)': each value binds to its own test (quoted glosses ignored). "Hemoglobin, white cell counts and platelets are all normal" is a group of exactly those three. "Near the lower limit", "toward the upper end" are never LOW/HIGH. |
| Recall classes (lexicon) | "No obvious emergency here", "no life-threatening red flags", "clinically insignificant", "your kidneys are functioning normally", "core organs", "this confirms the heart is healthy", "rules out" → OVERSTEPS. Lists after "can be associated with:", "can cause:", "risk of:", "What can cause …?" are one cause-list passage. Placing results "into the borderline category" is a health judgement. |

**This set is no longer held-out either.** All three sets are training data, ratcheted in `tests/test_benchmark.py` with 0 false alarms required (`score.json` → `sets.heldout_round2`), and the anti-special-casing guard now scans all three sets' sentences. **Round 3 needs fresh answers that nobody has tuned on** (ideally new report types, e.g. histopathology, a different chatbot), scored once on arrival.
