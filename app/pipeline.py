"""Orchestrates parsing -> comparison -> term matching -> classification -> safety."""
from __future__ import annotations

import re
from typing import Callable, Optional

from app.classify import find_hedges
from app.glossary.matcher import find_terms, find_unrecognized
from app.models import (OUT_OF_RANGE, AnalysisResult, ExplanationItem, LabValue, ReferenceRange, Span, Status,
                        Summary, Tag)
from app.parser.lines import Line, normalize_text, segment
from app.parser.ranges import (compare_numeric, compare_qualitative, parse_range, select_alternative,
                               units_compatible)
from app.parser.narrative import narrative_values
from app.parser.values import RawRow, parse_line

COLUMN_ROW = re.compile(r"\S(?:\t| {3,})\S")      # a table row the row parser rejected stays rejected
from app.safety import DISCLAIMER, out_of_range_message, sanitize

SEX_RE = re.compile(
    r"(?:age\s*/\s*sex|age\s*/\s*gender|sex|gender)\s*[:\-]?\s*(?:\d+\s*(?:y(?:rs?|ears?)?)?\.?\s*/\s*)?"
    r"(?P<sex>male|female|m|f)(?![A-Za-z])",
    re.I,
)
FLAG_DIRECTION = {"h": "above", "hh": "above", "high": "above", "hi": "above", "↑": "above",
                  "l": "below", "ll": "below", "low": "below", "lo": "below", "↓": "below"}


AGE_RE = re.compile(r"\bage(?:\s*/\s*(?:sex|gender))?\s*[:\-]?\s*(?P<age>\d{1,3})\s*(?:y|yrs?|years?)?\b", re.I)


def detect_age(text: str) -> Optional[float]:
    head = "\n".join(text.split("\n")[:25])
    m = AGE_RE.search(head)
    return float(m["age"]) if m and int(m["age"]) <= 120 else None


def detect_sex(text: str) -> Optional[str]:
    head = "\n".join(text.split("\n")[:25])
    m = SEX_RE.search(head)
    if not m:
        return None
    return "male" if m["sex"].lower().startswith("m") else "female"


def _fmt_value(v: LabValue) -> str:
    return f"{v.value_raw}{(' ' + v.unit) if v.unit else ''}"


FINDING_NOTE = "This explains the word only. Whether it applies to you is for your doctor to say."
NO_RANGE_TEXT = ("Your report does not print a reference range for this test, so MedLens does not compare it. "
                 "We never use outside 'normal' values. Ask the lab or your doctor what range applies.")
PLACEHOLDER_RE = re.compile(
    r"^\s*[(\[]?\s*(?:-+|—|–|na|n/a|n\.a\.|nil|not\s+applicable|see\s+notes?|refer\s+(?:to\s+)?(?:the\s+)?table|"
    r"as\s+per\s+clinical\s+context|refer\s+notes?)\s*[)\]]?\s*$", re.I)
MISSING_REASON_TEXT = {
    "no_range_printed": "No range is printed for this test.",
    "placeholder": "The range column only shows a placeholder ('{raw}'), not a range.",
    "range_unreadable": "A range is printed ('{raw}') but MedLens could not read it safely.",
    "unit_mismatch": "The range is printed in a different unit than the result.",
    "qualitative_no_reference": "This is a word result (like Positive/Negative) with no expected result printed.",
    "other_row_only": "A range is printed for another row of this test; it is not applied to this row.",
}


def _missing(v: LabValue, reason: str, raw: str = "") -> None:
    v.missing_range_reason = reason
    v.status = Status.UNIT_MISMATCH if reason == "unit_mismatch" else (
        Status.UNPARSEABLE if reason == "range_unreadable" else Status.NO_RANGE)
    v.status_explanation = MISSING_REASON_TEXT[reason].format(raw=raw) + " " + NO_RANGE_TEXT


def compute_status(v: LabValue, patient_sex: Optional[str], patient_age: Optional[float] = None) -> None:
    """Fill status / status_explanation / applied_range in place."""
    r = v.reference
    if r is None:
        if v.reference_span is not None and PLACEHOLDER_RE.match(v.reference_span.text):
            return _missing(v, "placeholder", v.reference_span.text.strip())
        return _missing(v, "qualitative_no_reference" if v.value_qualitative else "no_range_printed")
    if r.kind == "unparseable":
        return _missing(v, "range_unreadable", r.raw)
    applied: Optional[ReferenceRange] = r
    if r.kind == "multi":
        applied, note = select_alternative(r, patient_sex, patient_age)
        if applied is None:
            v.status = Status.AMBIGUOUS
            v.status_explanation = note
            return
        v.notes.append(note)
    v.applied_range = applied
    disp = applied.display()

    if applied.kind == "qualitative":
        if v.value_qualitative is None:
            v.status = Status.INDETERMINATE
            v.status_explanation = "The result is a number but the printed reference is a word, so no comparison was made."
            return
        v.status = compare_qualitative(v.value_qualitative, applied)
        if v.status == Status.WITHIN:
            v.status_explanation = f"The result '{v.value_qualitative}' matches the reference printed on your report ('{disp}')."
        elif v.status == Status.DIFFERS:
            v.status_explanation = (f"The result '{v.value_qualitative}' differs from the reference printed on your report "
                                    f"('{disp}'). What this means for you depends on your overall health and history — "
                                    "discuss it with your doctor.")
        else:
            v.status_explanation = (f"MedLens could not reliably compare '{v.value_qualitative}' with the printed "
                                    f"reference '{disp}', so it is not flagged.")
        return

    if v.value_num is None:
        v.status = Status.INDETERMINATE
        v.status_explanation = "The result is a word but the printed reference is numeric, so no comparison was made."
        return
    if not units_compatible(v.unit, applied.unit):
        _missing(v, "unit_mismatch")
        v.status_explanation = (f"The result's unit ({v.unit}) differs from the unit printed with the range "
                                f"({applied.unit}). MedLens does not convert units, so no comparison was made. "
                                + NO_RANGE_TEXT)
        return
    v.status = compare_numeric(v.value_num, v.value_comparator, applied, v.value_high)
    if v.status == Status.WITHIN:
        v.status_explanation = f"The value {_fmt_value(v)} is within the reference range printed on your report ({disp})."
    elif v.status in (Status.ABOVE, Status.BELOW):
        v.status_explanation = out_of_range_message("above" if v.status == Status.ABOVE else "below", disp)
    else:
        v.status_explanation = (f"The reported value ({v.value_raw}) is a limit or a span, not one exact number, and it overlaps "
                                f"the printed range ({disp}), so MedLens cannot tell whether it is inside the range.")


def check_flag(v: LabValue) -> None:
    if not v.flag_raw:
        if v.status in (Status.ABOVE, Status.BELOW):
            v.notes.append("The report itself does not print a flag next to this value.")
        return
    f = v.flag_raw.strip().lower()
    expected = FLAG_DIRECTION.get(f, "abnormal")
    computed = {Status.ABOVE: "above", Status.BELOW: "below", Status.DIFFERS: "abnormal", Status.WITHIN: "within"}.get(v.status)
    if computed is None:
        v.notes.append(f"The report flags this value as '{v.flag_raw}'. MedLens could not independently check it.")
        return
    agree = (computed == expected) or (expected == "abnormal" and computed in ("above", "below", "abnormal"))
    if not agree:
        v.flag_disagreement = True
        v.notes.append(f"Report flag disagrees with computed comparison: the report marks '{v.flag_raw}', but "
                       f"comparing {v.value_raw} with the printed range gives '{v.status.value}'. "
                       "Please confirm with the lab or your doctor.")


def _to_labvalue(i: int, ln: Line, raw: RawRow) -> LabValue:
    ref = parse_range(raw.range_raw) if raw.range_raw else None
    if raw.range_raw and PLACEHOLDER_RE.match(raw.range_raw) and not (
            raw.value_qualitative and ref is not None and ref.kind == "qualitative"):
        ref = None                     # "-", "NA", "See note" ... is not a range
    return LabValue(
        id=f"v{i}", line_number=ln.number, section=ln.section, test_name=raw.test_name, test_span=raw.test_span,
        value_raw=raw.value_raw, value_num=raw.value_num, value_comparator=raw.value_comparator,
        value_high=raw.value_high,
        value_qualitative=raw.value_qualitative, value_span=raw.value_span, unit=raw.unit, unit_span=raw.unit_span,
        flag_raw=raw.flag_raw, flag_span=raw.flag_span, reference=ref, reference_span=raw.range_span,
    )


REFERENCE_SECTION_RE = re.compile(
    r"^(interpretation|comments?|notes?|remarks?|risk\s+categor(?:y|ies)|clinical\s+significance)$", re.I)
REFERENCE_LABEL_RE = re.compile(
    r"(?:(?:extreme|very\s+high|high|moderate|intermediate|low)\s+risk(?:\s+group)?(?:\s+.*)?|risk\s+categor(?:y|ies)|"
    r"optimal|near\s+optimal|above\s+optimal|borderline(?:\s+high|\s+low)?|high|very\s+high|extreme|low|desirable|"
    r"undesirable|(?:1st|2nd|3rd|first|second|third)\s+trimester|trimester\s*(?:1|2|3|i|ii|iii)|interpretation|"
    r"comments?|notes?|remarks?)", re.I)


def _reference_row(raw: RawRow) -> bool:
    """A 'row' that is reference material, not a result: a risk/trimester label, or a value that is
    itself a range or comparator (<100, 0.100 - 2.500) with no unit."""
    if REFERENCE_LABEL_RE.fullmatch(raw.test_name.strip()):
        return True
    return (raw.value_comparator is not None or raw.value_high is not None) and not raw.unit


def _full_result_row(raw: RawRow) -> bool:
    return (raw.value_num is not None and raw.value_comparator is None and raw.value_high is None and bool(raw.unit)
            and bool(raw.range_raw) and parse_range(raw.range_raw).kind != "unparseable" and not _reference_row(raw))


def _line_span(ln: Line) -> Span:
    lead = len(ln.text) - len(ln.text.lstrip())
    s = ln.text.strip()
    return Span(start=ln.start + lead, end=ln.start + lead + len(s), text=s)


# Optional hook for an LLM fallback (see app/llm.py). Signature: (term_text, context) -> str | None
UnknownTermExplainer = Callable[[str, str], Optional[str]]


OPINION_NORMAL = re.compile(r"\b(?:within\s+normal\s+limits|normal\s+(?:study|ecg|scan|tracing))\b", re.I)
OPINION_EXCEPT = re.compile(r"\b(?:except|but|however|apart\s+from|other\s+than|borderline|abnormal)\b", re.I)


def _opinion_normal(values: list[LabValue], lines: list[Line], sections) -> None:
    """A value printed without a range in a study section, followed by the reporting doctor's opinion
    on that study saying it is normal ("Opinion: ECG within normal limits."): the report settles NORMAL."""
    for v in values:
        if v.status != Status.NO_RANGE or v.section is None:
            continue
        k = next((i for i, sec in enumerate(sections) if sec.name == v.section and sec.start <= v.value_span.start <= sec.end), None)
        if k is None or k + 1 >= len(sections) or not sections[k + 1].interpretive:
            continue
        nxt = sections[k + 1]
        for ln in lines:
            if nxt.start <= ln.start <= nxt.end and OPINION_NORMAL.search(ln.text) and not OPINION_EXCEPT.search(ln.text):
                v.opinion_normal_span = _line_span(ln)
                break


def analyze(text: str, explain_unknown: Optional[UnknownTermExplainer] = None) -> AnalysisResult:
    text = normalize_text(text)
    lines, sections = segment(text)
    sex = detect_sex(text)
    warnings: list[str] = []

    # ---- reference sections ("Interpretation", "Risk Category", "Note") end at the first real result row
    for sec in sections:
        if not (sec.interpretive and REFERENCE_SECTION_RE.match(sec.name)):
            continue
        sec_lines = [ln for ln in lines if ln.section == sec.name and sec.start <= ln.start <= sec.end]
        for k, ln in enumerate(sec_lines[1:], start=1):
            raw = parse_line(ln)
            if raw and _full_result_row(raw):
                for later in sec_lines[k:]:
                    later.section, later.interpretive = None, False
                sec.end = max(sec.start, ln.start - 1)
                break

    # ---- values
    values: list[LabValue] = []
    age = detect_age(text)
    prev_was_value = False
    for ln in lines:
        if ln.interpretive:
            prev_was_value = False
            continue    # never treat opinion text as a measured result
        stripped = ln.text.strip()
        # indented "Children 1-5 yrs: 0.7-6.0" under a result row continues that row's labelled ranges
        if prev_was_value and values and stripped and re.match(r"^\s{4,}", ln.text):
            cont = parse_range(stripped)
            if cont.kind == "multi":
                v = values[-1]
                base = v.reference
                if base is not None and base.kind in ("multi", "interval", "upper", "lower"):
                    alts = list(base.alternatives) if base.kind == "multi" else []
                    if base.kind != "multi":
                        alts = []   # unlabelled first range + labelled continuation: keep as unparseable-safe multi
                    merged = base.model_copy(update={"kind": "multi", "raw": base.raw + " | " + stripped,
                                                     "alternatives": alts + cont.alternatives})
                    if base.kind != "multi":
                        merged = ReferenceRange(raw=merged.raw, kind="unparseable")
                    v.reference = merged
                    v.notes.append("The printed range continues on the next line of the report.")
                continue
        raw = parse_line(ln)
        if raw and _reference_row(raw):
            raw = None          # risk tables, trimester ranges...: reference material, shown as plain text
        prev_was_value = bool(raw)
        if raw:
            values.append(_to_labvalue(len(values), ln, raw))
        elif not COLUMN_ROW.search(ln.text):     # prose: "Heart Rate: 88 bpm. Normal sinus rhythm."
            for nr in narrative_values(ln.text, ln.start):
                values.append(_to_labvalue(len(values), ln, nr))
    for v in values:
        compute_status(v, sex, age)
        check_flag(v)
    _opinion_normal(values, lines, sections)
    # same test printed twice, only one row has a range: never borrow it
    by_name: dict[str, list[LabValue]] = {}
    for v in values:
        by_name.setdefault(v.test_name.lower(), []).append(v)
    for rows in by_name.values():
        if len(rows) > 1 and any(r.reference is not None for r in rows):
            for v in rows:
                if v.reference is None:
                    _missing(v, "other_row_only")
                    v.notes.append("range printed for another row of this test; not applied")

    # ---- terms
    terms = find_terms(text)
    for v in values:
        for t in terms:
            if v.test_span.start <= t.span.start and t.span.end <= v.test_span.end:
                v.glossary_term_id = t.term_id
                break
    known = [(t.span.start, t.span.end) for t in terms]
    for v in values:
        for sp in (v.value_span, v.unit_span, v.reference_span, v.flag_span):
            if sp:
                known.append((sp.start, sp.end))
    unrecognized = find_unrecognized(text, known)

    # ---- explanations
    ex: list[ExplanationItem] = []

    def passage_for(span: Optional[Span], block: bool = False) -> Optional[Span]:
        if span is None:
            return None
        if block:
            sec = next((x for x in sections if x.start <= span.start <= x.end), None)
            if sec is not None:
                seg = text[sec.start:sec.end]
                lead = len(seg) - len(seg.lstrip())
                body = seg.strip()
                return Span(start=sec.start + lead, end=sec.start + lead + len(body), text=body)
        ln = next(l for l in lines if l.start <= span.start <= l.end)
        return _line_span(ln)

    def add(tag: Tag, title: str, body: str, snippet: str, span: Optional[Span], related: Optional[str],
            origin: str = "rule", block: bool = False) -> None:
        safe, filtered = sanitize(body)
        ex.append(ExplanationItem(id=f"e{len(ex)}", tag=tag, title=title, text=safe, source_snippet=snippet,
                                  span=span, passage=passage_for(span, block), related_id=related, origin=origin,
                                  safety_filtered=filtered))

    line_by_no = {ln.number: ln for ln in lines}
    for v in values:
        snippet = line_by_no[v.line_number].text.strip()
        rng = v.reference.display() if v.reference else "none printed"
        facts = f"Report value: {_fmt_value(v)}. Reference range printed on report: {rng}."
        if v.flag_raw:
            facts += f" Report flag: {v.flag_raw}."
        facts += f" Computed comparison: {v.status.value.replace('_', ' ')}."
        if v.status in (Status.WITHIN, Status.NO_RANGE, Status.UNPARSEABLE, Status.UNIT_MISMATCH, Status.INDETERMINATE):
            facts += " " + v.status_explanation
        add(Tag.FACTUAL, f"{v.test_name}: {v.value_raw}", facts, snippet, v.value_span, v.id)
        if v.status in OUT_OF_RANGE or v.status == Status.AMBIGUOUS:
            add(Tag.PROFESSIONAL, f"{v.test_name}: {v.status.value.replace('_', ' ')}", v.status_explanation,
                snippet, v.value_span, v.id)
        if v.flag_disagreement:
            add(Tag.PROFESSIONAL, f"{v.test_name}: report flag disagrees", v.notes[-1], snippet,
                v.flag_span or v.value_span, v.id)

    abnormal = [v for v in values if v.status in OUT_OF_RANGE]
    if len(abnormal) >= 2:
        names = ", ".join(v.test_name for v in abnormal)
        add(Tag.PROFESSIONAL, "Several values outside printed ranges",
            f"{len(abnormal)} values are outside the ranges printed on the report ({names}). Patterns across "
            "several tests can only be interpreted by a clinician who knows your history.", names, None, None)

    seen_terms: set[str] = set()
    for t in terms:
        if t.term_id in seen_terms:
            continue
        seen_terms.add(t.term_id)
        body = t.plain_explanation
        if t.ambiguous:
            body += " Possible meanings: " + "; ".join(t.possibilities) + "."
        if t.what_it_is_not:
            body += " Note: " + t.what_it_is_not
        if t.kind == "finding":
            body += " " + FINDING_NOTE
        ln = next(l for l in lines if l.start <= t.span.start <= l.end)
        add(Tag.GENERAL, t.term, body, ln.text.strip(), t.span, t.term_id, origin="glossary")

    for ln in lines:
        if not ln.text.strip():
            continue
        hedges = find_hedges(ln.text)
        is_header_only = any(s.start == ln.start for s in sections) and not re.search(r":\s*\S", ln.text)
        if ln.interpretive and not is_header_only:
            body = (f"This line is in the '{ln.section}' section, which is the reporting doctor's professional "
                    "opinion. MedLens does not interpret it; only your clinician can explain what it means for you.")
            if hedges:
                body += " It also uses hedged wording (" + ", ".join(f"'{h.group(0).strip()}'" for h in hedges) + \
                        "), which describes a possibility, not a certainty."
            add(Tag.PROFESSIONAL, f"{ln.section}: professional opinion", body, ln.text.strip(), _line_span(ln), None,
                block=True)
        elif hedges and not ln.interpretive:
            h = hedges[0]
            add(Tag.PROFESSIONAL, "Hedged language",
                f"This sentence uses hedged wording ('{h.group(0).strip()}'): the report author is describing a "
                "possibility, not a certainty. Ask your clinician what it means for you.",
                ln.text.strip(), _line_span(ln), None)

    for u in unrecognized:
        meaning = None
        if explain_unknown:
            try:
                ctx_line = next(l.text for l in lines if l.start <= u.span.start <= l.end)
                meaning = explain_unknown(u.text, ctx_line)
            except Exception as exc:  # LLM is optional; never break the pipeline
                warnings.append(f"LLM fallback failed for '{u.text}': {exc.__class__.__name__}")
        if meaning:
            add(Tag.GENERAL, f"{u.text} (AI-generated, unverified)",
                "AI-generated, unverified: " + meaning + " Confirm this with your clinician.",
                u.text, u.span, u.id, origin="llm")
        else:
            add(Tag.PROFESSIONAL, f"Unrecognized term: {u.text}", u.reason, u.text, u.span, u.id)

    # ---- summary
    st = [v.status for v in values]
    summary = Summary(
        terms_found=len(seen_terms), values_parsed=len(values),
        out_of_range=sum(s in OUT_OF_RANGE for s in st), within_range=st.count(Status.WITHIN),
        ambiguous=st.count(Status.AMBIGUOUS), no_range_supplied=st.count(Status.NO_RANGE),
        other_status=sum(s in (Status.UNIT_MISMATCH, Status.INDETERMINATE, Status.UNPARSEABLE) for s in st),
        flag_disagreements=sum(v.flag_disagreement for v in values), unrecognized_terms=len(unrecognized),
        needs_professional=sum(e.tag == Tag.PROFESSIONAL for e in ex),
    )
    if not values and not terms:
        warnings.append("No lab values or known medical terms were found in this text.")
    result = AnalysisResult(text=text, patient_sex=sex, sections=sections, values=values, terms=terms,
                            unrecognized=unrecognized, explanations=ex, summary=summary, disclaimer=DISCLAIMER,
                            warnings=warnings)
    from app.summary import build_summary
    result.structured_summary = build_summary(result, age)
    return result
