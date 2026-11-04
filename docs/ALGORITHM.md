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
* **Testable.** 108 unit and end-to-end tests pin the behaviour, and the same input always gives the same output.
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
