"""Check claims from an AI explanation against what the report actually says (rule-based)."""
from __future__ import annotations

import math
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.models import OUT_OF_RANGE, AnalysisResult, LabValue, ReferenceRange, Span, Status
from app.parser.ranges import units_compatible
from app.safety import is_safe
from app.pipeline import analyze
from app.glossary.matcher import find_terms, load_glossary
from app.verify.claims import GROUP_TERMS, GROUPS, NORMAL_CATEGORIES, Claim, extract, strip_markdown
from app.verify.names import names_match

Label = Literal["VERIFIED", "CONTRADICTED", "RANGE_NOT_FROM_REPORT", "OVERSTEPS", "NOT_IN_REPORT",
                "UNVERIFIABLE", "NEUTRAL"]
# most severe first: a sentence takes the label of its most severe finding
SEVERITY: list[str] = ["CONTRADICTED", "OVERSTEPS", "RANGE_NOT_FROM_REPORT", "NOT_IN_REPORT", "UNVERIFIABLE",
                       "VERIFIED", "NEUTRAL"]
OVERSTEP_MSG = "This goes beyond what a report explanation should say; ask your doctor."
OVERSTEP_KIND = {"diagnosis": "It names a condition ('{p}'), which is a diagnosis only your doctor can make.",
                 "treatment": "It tells you to take or change a medicine, or how much of it ('{p}').",
                 "reassurance": "It gives reassurance that only your doctor can give ('{p}').",
                 "interpretation": "It turns the results into a statement about your health ('{p}'), which only "
                                   "your doctor can make.",
                 "link": "This links your results to a condition or cause ('{p}'). That is an interpretation only your "
                         "doctor can make.",
                 "condition": "This links your results to a condition, cause or health judgement ('{p}'), which only "
                              "a doctor can interpret."}

# An AI may name a test more generally (or by a sibling name) than the report does.
RELATED_TESTS = {
    "white_blood_cell_count": {"total_leukocyte_count"},
    "total_leukocyte_count": {"white_blood_cell_count"},
    "pcv": {"packed_cell_volume"},
    "packed_cell_volume": {"pcv"},
    "bilirubin": {"total_bilirubin"},
    "glucose": {"fasting_blood_sugar", "random_blood_sugar", "post_prandial_blood_sugar"},
    "prothrombin_time": {"international_normalized_ratio"},
}


class Finding(BaseModel):
    kind: Literal["value", "range", "status", "overstep", "mention", "group", "flag", "outside_range", "quote", "certainty",
                  "passage"]
    label: Label
    message: str
    term: Optional[str] = None
    ai_span: Span
    report_span: Optional[Span] = None
    report_value_id: Optional[str] = None
    extra_report_spans: list[Span] = Field(default_factory=list)    # group claims: every report line involved


class VerifiedSentence(BaseModel):
    id: str
    text: str
    span: Span
    label: Label
    findings: list[Finding] = Field(default_factory=list)
    passage_id: Optional[str] = None     # consecutive OVERSTEPS items of one list are one passage
    passage_size: int = 1


class Omission(BaseModel):
    value_id: str
    test_name: str
    value_raw: str
    status: Status
    message: str
    report_span: Span


class VerifyResult(BaseModel):
    sentences: list[VerifiedSentence]
    omissions: list[Omission]
    counts: dict[str, int]
    claims_checked: int
    report_analysis: AnalysisResult
    note: str = ("MedLens checks what the AI said against your report. It does not judge medical meaning. "
                 "Only your doctor can.")


# ------------------------------------------------------------------ report index
def _line_span(text: str, pos: int) -> Span:
    a = text.rfind("\n", 0, pos) + 1
    b = text.find("\n", pos)
    b = len(text) if b == -1 else b
    seg = text[a:b]
    lead = len(seg) - len(seg.lstrip())
    s = seg.strip()
    return Span(start=a + lead, end=a + lead + len(s), text=s)


def _index(r: AnalysisResult) -> dict[str, list[LabValue]]:
    """term_id -> report rows whose test name contains that term."""
    idx: dict[str, list[LabValue]] = {}
    for v in r.values:
        for t in r.terms:
            if v.test_span.start <= t.span.start and t.span.end <= v.test_span.end:
                idx.setdefault(t.term_id, [])
                if v not in idx[t.term_id]:
                    idx[t.term_id].append(v)
    return idx


def _rows_for(term_id: str, idx: dict[str, list[LabValue]], values: list[LabValue] = (),
              mention_text: str = "") -> list[LabValue]:
    rows = list(idx.get(term_id, []))
    for other in RELATED_TESTS.get(term_id, ()):
        rows += [v for v in idx.get(other, []) if v not in rows]
    if not rows and values:
        # fallback: order-independent normalised names ("Cholesterol, Total" == "Total Cholesterol")
        e = load_glossary().get(term_id, {})
        candidates = [mention_text, e.get("term", ""), *e.get("aliases", [])]
        rows = [v for v in values if any(c and names_match(c, v.test_name) for c in candidates)]
    return rows


def _same(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-9)


def _ranges_equal(ai: ReferenceRange, rep: ReferenceRange) -> bool:
    """Same kind and same bounds. Inclusive/exclusive wording differences are tolerated."""
    if ai.kind == "multi":                 # "Men >40": each labelled range the AI quotes must be printed
        return bool(ai.alternatives) and all(_ranges_equal(alt.range, rep) for alt in ai.alternatives)
    if rep.kind == "multi":
        return any(_ranges_equal(ai, alt.range) for alt in rep.alternatives)
    if ai.kind != rep.kind:
        return False
    if ai.kind == "interval":
        return _same(ai.low, rep.low) and _same(ai.high, rep.high)
    if ai.kind == "upper":
        return _same(ai.high, rep.high)
    if ai.kind == "lower":
        return _same(ai.low, rep.low)
    return False


STATUS_WORD = {Status.ABOVE: "higher than", Status.BELOW: "lower than", Status.WITHIN: "within",
               Status.DIFFERS: "different from"}
CLAIM_WORD = {"LOW": "low", "HIGH": "high", "NORMAL": "normal / within range"}


def _report_status(v: LabValue) -> Optional[str]:
    st = {Status.ABOVE: "HIGH", Status.BELOW: "LOW", Status.WITHIN: "NORMAL"}.get(v.status)
    if st is None and v.status == Status.AMBIGUOUS:
        return _settled_across_ranges(v)
    return st


def _settled_across_ranges(v: LabValue) -> Optional[str]:
    """Several printed ranges (M/F, age bands): if the value is on the same side of ALL of them, the report does
    settle it (Uric Acid 7.4 vs M 3.4-7.0 and F 2.4-5.7 is above both). Within all of them is not treated as
    settled: a pregnancy or age band may still change what applies."""
    from app.parser.ranges import compare_numeric
    r = v.reference
    if r is None or r.kind != "multi" or v.value_num is None or not r.alternatives:
        return None
    results = set()
    for alt in r.alternatives:
        if alt.range.kind not in ("interval", "upper", "lower"):
            return None
        results.add(compare_numeric(v.value_num, v.value_comparator, alt.range, v.value_high))
    # only "outside ALL ranges in the same direction" settles it; within all of them stays unsettled
    return {frozenset({Status.ABOVE}): "HIGH", frozenset({Status.BELOW}): "LOW"}.get(frozenset(results))


def _unsettled_reason(v: LabValue) -> str:
    if v.status == Status.AMBIGUOUS:
        return "the report lists more than one range for this test and doesn't say which applies"
    if v.status in (Status.NO_RANGE, Status.UNPARSEABLE, Status.UNIT_MISMATCH):
        return "the report prints no usable range for this test"
    return "the comparison with the printed range is not clear-cut"


# ------------------------------------------------------------------ checking
def _check_category(c: Claim, text: str, rows: list[LabValue]) -> Finding:
    """A named category with a threshold (e.g. 'Borderline high is 200 to 239'): fine only if the report
    prints that same category table."""
    nums = [x for x in (c.range.low, c.range.high) if x is not None]
    printed = re.search(rf"\b{re.escape(c.category)}\b", text, re.I) and all(
        re.search(rf"(?<![\d.]){re.escape(f'{n:g}')}(?:0*)(?![\d])", text) for n in nums)
    rep = _line_span(text, rows[0].value_span.start) if rows else None
    if printed:
        return Finding(kind="range", label="VERIFIED", term=c.term, ai_span=c.span, report_span=rep,
                       message=f"Your report prints this '{c.category}' category.")
    return Finding(kind="range", label="RANGE_NOT_FROM_REPORT", term=c.term, ai_span=c.span, report_span=rep,
                   report_value_id=rows[0].id if rows else None,
                   message=f"AI quotes a '{c.category}' category ({c.range.display()}) that your report does not print. "
                           "MedLens only uses the ranges printed on your report.")


def _check_claim(c: Claim, text: str, rows: list[LabValue], value_row: Optional[LabValue]) -> Finding:
    span = c.span
    if c.kind == "overstep":
        norm = lambda x: re.sub(r"\s+", " ", strip_markdown(x)).strip().lower()  # noqa: E731
        phrase = re.sub(r"\s+", " ", c.detail).strip() if c.detail and norm(c.detail) in norm(span.text) else ""
        why = OVERSTEP_KIND[c.overstep]
        if phrase and is_safe(phrase):
            why = why.format(p=phrase)
        else:                           # never repeat an unsafe phrase in MedLens's own words
            why = re.sub(r"\s*\('\{p\}'\)", "", why)
        return Finding(kind="overstep", label="OVERSTEPS", message=f"{OVERSTEP_MSG} {why}", ai_span=span)
    if not rows:
        return Finding(kind=c.kind, label="NOT_IN_REPORT", term=c.term, ai_span=span,
                       message=f"Your report has no result for {c.term}, so this can't be checked against it.")
    rep_line = lambda v: _line_span(text, v.value_span.start)  # noqa: E731

    if c.kind == "flag":
        mism = [v for v in rows if v.flag_disagreement]
        if mism:
            return Finding(kind="flag", label="VERIFIED", term=c.term, ai_span=span, report_span=rep_line(mism[0]),
                           report_value_id=mism[0].id,
                           message="The AI correctly noted the lab's flag doesn't match the printed range.")
        return Finding(kind="flag", label="CONTRADICTED", term=c.term, ai_span=span, report_span=rep_line(rows[0]),
                       report_value_id=rows[0].id,
                       message=f"The AI says the lab's flag for {rows[0].test_name} doesn't match the printed range, "
                               "but on your report the flag and the range agree.")

    if c.kind == "value":
        for v in rows:
            if v.value_num is None:
                continue
            hi = c.value_high if c.value_high is not None else c.value
            v_hi = v.value_high if v.value_high is not None else v.value_num
            if _same(c.value, v.value_num) and _same(hi, v_hi):
                note = ""
                if c.unit and v.unit and re.sub(r"\W", "", c.unit.lower()) != re.sub(r"\W", "", v.unit.lower()):
                    note = f" (the AI writes the unit as {c.unit}; the report uses {v.unit})"
                return Finding(kind="value", label="VERIFIED", term=c.term, ai_span=span, report_span=rep_line(v),
                               report_value_id=v.id,
                               message=f"Matches your report: {v.test_name} {v.value_raw}{' ' + v.unit if v.unit else ''}.{note}")
        shown = " or ".join(f"{v.value_raw}{' ' + v.unit if v.unit else ''}" for v in rows)
        if c.unit and all(v.unit and not units_compatible(c.unit, v.unit) for v in rows):
            return Finding(kind="value", label="UNVERIFIABLE", term=c.term, ai_span=span, report_span=rep_line(rows[0]),
                           report_value_id=rows[0].id,
                           message=f"The AI uses a different unit ({c.unit}) than your report ({shown}). MedLens does "
                                   "not convert units, so it cannot check this number.")
        return Finding(kind="value", label="CONTRADICTED", term=c.term, ai_span=span, report_span=rep_line(rows[0]),
                       report_value_id=rows[0].id, message=f"AI says {c.value_raw}, report says {shown}.")

    if c.kind == "range" and c.category and c.category not in NORMAL_CATEGORIES:
        return _check_category(c, text, rows)
    if c.kind == "range":
        with_range = [v for v in rows if v.reference is not None and v.reference.kind != "unparseable"]
        for v in with_range:
            if _ranges_equal(c.range, v.reference):
                return Finding(kind="range", label="VERIFIED", term=c.term, ai_span=span, report_span=rep_line(v),
                               report_value_id=v.id,
                               message=f"Matches the range printed on your report ({v.reference_span.text}).")
        if not with_range:
            v = rows[0]
            return Finding(kind="range", label="RANGE_NOT_FROM_REPORT", term=c.term, ai_span=span,
                           report_span=rep_line(v), report_value_id=v.id,
                           message="Your report prints no range for this test; this range came from elsewhere.")
        v = with_range[0]
        return Finding(kind="range", label="RANGE_NOT_FROM_REPORT", term=c.term, ai_span=span, report_span=rep_line(v),
                       report_value_id=v.id,
                       message=f"AI quotes {c.range.display()}; your report prints {v.reference_span.text}.")

    # status
    candidates = [value_row] if value_row is not None else rows
    v = candidates[0]
    computed = {(_report_status(x) or x.status.value) for x in candidates}
    if len(candidates) > 1 and len(computed) > 1:
        return Finding(kind="status", label="UNVERIFIABLE", term=c.term, ai_span=span, report_span=rep_line(v),
                       report_value_id=v.id,
                       message="The report doesn't settle this: it has several rows for this test with different results.")
    rs = _report_status(v)
    if rs is None:
        if v.status == Status.DIFFERS and c.status == "NORMAL":
            return Finding(kind="status", label="CONTRADICTED", term=c.term, ai_span=span, report_span=rep_line(v),
                           report_value_id=v.id,
                           message=f"AI says normal; your report's result ({v.value_raw}) differs from the expected "
                                   f"result printed on it ({v.reference_span.text if v.reference_span else ''}).")
        return Finding(kind="status", label="UNVERIFIABLE", term=c.term, ai_span=span, report_span=rep_line(v),
                       report_value_id=v.id, message=f"The report doesn't settle this: {_unsettled_reason(v)}.")
    rng = v.applied_range.display() if v.applied_range else (v.reference_span.text if v.reference_span else "")
    if v.status == Status.AMBIGUOUS:
        word = {"HIGH": "above", "LOW": "below", "NORMAL": "within"}[rs]
        if rs == c.status:
            return Finding(kind="status", label="VERIFIED", term=c.term, ai_span=span, report_span=rep_line(v),
                           report_value_id=v.id,
                           message=f"Matches: {v.value_raw} is {word} every range printed for this test ({rng}).")
        return Finding(kind="status", label="UNVERIFIABLE" if c.weak else "CONTRADICTED", term=c.term, ai_span=span,
                       report_span=rep_line(v), report_value_id=v.id,
                       message=f"AI says {CLAIM_WORD[c.status]}; {v.value_raw} is {word} every range printed for "
                               f"this test ({rng}).")
    if rs == c.status:
        return Finding(kind="status", label="VERIFIED", term=c.term, ai_span=span, report_span=rep_line(v),
                       report_value_id=v.id,
                       message=f"Matches: {v.value_raw} is {STATUS_WORD[v.status]} the range printed on your report ({rng}).")
    if c.weak:                     # precision first: an indirect reference is never called a contradiction
        return Finding(kind="status", label="UNVERIFIABLE", term=c.term, ai_span=span, report_span=rep_line(v),
                       report_value_id=v.id,
                       message=f"This sentence doesn't name {v.test_name}; if it means it, the report shows {v.value_raw} "
                               f"is {STATUS_WORD[v.status]} the printed range ({rng}).")
    return Finding(kind="status", label="CONTRADICTED", term=c.term, ai_span=span, report_span=rep_line(v),
                   report_value_id=v.id,
                   message=f"AI says {CLAIM_WORD[c.status]}; compared with the range printed on your report ({rng}), "
                           f"{v.value_raw} is {STATUS_WORD[v.status]} it.")


# glossary category says CBC, but labs report ESR as its own test: only a CBC section heading makes it a blood count
CATEGORY_EXCEPTIONS = {"cbc": {"erythrocyte_sedimentation_rate"}}
SEX_WORDS = {"male": r"\b(?:men|man|male|males)\b", "female": r"\b(?:women|woman|female|females)\b"}


def _position(v: LabValue, r: ReferenceRange) -> str:
    from app.parser.ranges import compare_numeric
    st = compare_numeric(v.value_num, v.value_comparator, r, v.value_high)
    return {Status.WITHIN: "within", Status.ABOVE: "above", Status.BELOW: "below"}.get(st, "close to")


def _multi_range_assumptions(pairs, findings, analysis: AnalysisResult, age: Optional[float], sentence: str) -> None:
    """Several printed ranges and the header doesn't settle which applies: a claim that relies on ONE of them is
    UNVERIFIABLE (unless the value is on the same side of all of them); the message names the assumption."""
    from app.parser.ranges import label_band, _band_match
    sex = analysis.patient_sex
    by_id = {v.id: v for v in analysis.values}
    for c, f in pairs:
        v = by_id.get(f.report_value_id or "")
        if v is None or v.status != Status.AMBIGUOUS or v.reference is None or v.reference.kind != "multi":
            continue
        if v.value_num is None or _settled_across_ranges(v) is not None:
            continue
        alts = v.reference.alternatives
        applies_to_value = any(g.report_value_id == v.id and g.kind in ("value", "status") for g in findings)
        quoted = {id(a) for cc, _ in pairs if cc.kind == "range" and cc.range is not None
                  for a in alts if _ranges_equal(cc.range, a.range)}
        if f.kind == "range" and (not applies_to_value or len(quoted) == len(alts)):
            continue                                  # quoting printed ranges (one, or all of them) is fine
        if f.kind not in ("range", "status"):
            continue
        assumed = None
        if f.kind == "range" and c.range is not None:
            assumed = next((a for a in alts if _ranges_equal(c.range, a.range)), None)
        if assumed is None:
            for a in alts:
                b = label_band(a.label)
                if b.sex and re.search(SEX_WORDS[b.sex], sentence, re.I):
                    assumed = a
        f.label = "UNVERIFIABLE"
        f.message = _assumption_message(v, alts, assumed, sex, age)


def _assumption_message(v: LabValue, alts, assumed, sex: Optional[str], age: Optional[float]) -> str:
    from app.parser.ranges import label_band, _band_match
    bands = [label_band(a.label) for a in alts]
    if any(b.pregnancy for b in bands):
        preg = next(a for a, b in zip(alts, bands) if b.pregnancy)
        return (f"Your report also prints a pregnancy range ({preg.label}: {preg.range.display()}) and doesn't say "
                f"whether it applies, so MedLens can't tell which range to use.")
    if any(b.sex for b in bands) and sex is None:
        if assumed is not None:
            other = [a for a in alts if a is not assumed]
            others = "; ".join(f"for {label_band(a.label).sex and ('women' if label_band(a.label).sex == 'female' else 'men') or a.label} it prints {a.range.display()}"
                               for a in other)
            return f"The AI used the {assumed.label} range ({assumed.range.display()}). Your report doesn't state sex; {others}."
        return ("Your report prints different ranges for men and women ("
                + "; ".join(f"{a.label}: {a.range.display()}" for a in alts) + ") and doesn't state sex.")
    fit = [a for a, b in zip(alts, bands) if _band_match(b, sex, age) is not False] or alts
    who = f"age {age:g}" if age is not None else "this patient"
    pos = [_position(v, a.range) for a in fit]
    if len(fit) == 2 and pos[0] != pos[1]:
        tail = f"{v.value_raw} is {pos[0]} one and {pos[1]} the other."
    else:
        tail = f"{v.value_raw} is " + ", ".join(f"{p} {a.range.display()}" for p, a in zip(pos, fit)) + "."
    ranges = " and ".join(a.range.display() for a in fit)
    used = f"The AI used {assumed.range.display()}. " if assumed is not None and len(fit) > 1 else ""
    return f"{used}Your report prints {len(fit)} ranges that fit {who}: {ranges}. {tail}"


UNSETTLED = {Status.AMBIGUOUS, Status.NO_RANGE, Status.UNPARSEABLE, Status.UNIT_MISMATCH, Status.INDETERMINATE}
SIDE = {Status.ABOVE: "above", Status.BELOW: "below", Status.DIFFERS: "different from"}
GROUP_NAME = {"thyroid_hormones": "thyroid hormones", "liver": "liver tests", "kidney": "kidney tests", "cbc": "blood counts", "thyroid": "thyroid tests",
              "lipid": "lipid tests", "sugar": "blood sugar tests", "urine": "urine tests", "dlc": "differential counts",
              "all": "results"}


def _row_categories(v: LabValue, r: AnalysisResult) -> set[str]:
    gl = load_glossary()
    return {gl[t.term_id]["category"] for t in r.terms
            if v.test_span.start <= t.span.start and t.span.end <= v.test_span.end and t.term_id in gl}


def group_rows(group: str, heading_tests: set, r: AnalysisResult, idx: dict[str, list[LabValue]]) -> list[LabValue]:
    """Report rows that a group claim covers. A row's glossary category wins over its section heading, so
    Creatinine under 'LIVER & KIDNEY PANEL' is not a liver test, and ESR in its own section is not a blood count."""
    if group == "all":
        return list(r.values)
    if group in GROUP_TERMS:                       # e.g. differentials: only the differential tests themselves
        return [v for v in r.values if {t for t, vs in idx.items() if v in vs} & GROUP_TERMS[group]]
    _, section_re, cats = GROUPS[group]
    sec_rx = re.compile(section_re, re.I)
    any_group_section = re.compile("|".join(f"(?:{sr})" for _, sr, _ in GROUPS.values()), re.I)
    other_cats = {c for k, (_, _, cs) in GROUPS.items() if k != group for c in cs}
    listed = {id(v) for t in heading_tests for v in idx.get(t, [])}
    rows = []
    for v in r.values:
        vc = _row_categories(v, r)
        ids = {t for t, vs in idx.items() if v in vs}
        in_section = bool(v.section and sec_rx.search(v.section))
        unsorted_section = not v.section or not any_group_section.search(v.section)   # e.g. 'BIOCHEMISTRY'
        by_category = bool(vc & cats) and not (ids & CATEGORY_EXCEPTIONS.get(group, set()))
        if (id(v) in listed
                or (in_section and not (vc & other_cats and not vc & cats))
                or (unsorted_section and by_category)):
            rows.append(v)
    return rows


def _range_text(v: LabValue) -> str:
    t = v.reference_span.text if v.reference_span else (v.applied_range.display() if v.applied_range else "")
    return re.sub(r"(?<=\d)\s*-\s*(?=\d)", "–", t)


def _check_group(c: Claim, r: AnalysisResult, idx: dict[str, list[LabValue]]) -> Finding:
    rows = group_rows(c.group, c.group_tests, r, idx)
    name = GROUP_NAME.get(c.group, "results")
    if not rows:
        return Finding(kind="group", label="NOT_IN_REPORT", ai_span=c.span, term=name,
                       message=f"MedLens couldn't find the {name} on your report, so this can't be checked.")
    lines = [_line_span(r.text, v.value_span.start) for v in rows]
    out = [v for v in rows if v.status in OUT_OF_RANGE]
    unsettled = [v for v in rows if v.status in UNSETTLED]
    if out:
        parts = [f"{v.test_name} ({v.value_raw}) is {SIDE[v.status]} the range printed on your report ({_range_text(v)})"
                 for v in out]
        msg = "The AI says these are all within range, but " + "; ".join(parts) + "."
        if unsettled:
            msg += " The report doesn't settle " + ", ".join(v.test_name for v in unsettled) + "."
        bad = [_line_span(r.text, v.value_span.start) for v in out]
        return Finding(kind="group", label="CONTRADICTED", ai_span=c.span, term=name, report_span=bad[0],
                       report_value_id=out[0].id, extra_report_spans=bad, message=msg)
    if unsettled:
        return Finding(kind="group", label="UNVERIFIABLE", ai_span=c.span, term=name, report_span=lines[0],
                       extra_report_spans=lines,
                       message="The report doesn't settle this: " + ", ".join(v.test_name for v in unsettled)
                               + " have no single usable printed range.")
    return Finding(kind="group", label="VERIFIED", ai_span=c.span, term=name, report_span=lines[0], extra_report_spans=lines,
                   message=f"Matches: all {len(rows)} {name} on your report are within their printed ranges.")


# ---------------------------------------------------------------- quotes and certainty
def _norm_q(x: str) -> str:
    return " ".join(re.sub(r"[^\w\s?]", " ", strip_markdown(x).lower()).replace("?", " ? ").split())


def _best_quote_match(q: str, report: str) -> tuple[float, str, Optional[Span]]:
    """Best fuzzy match of quote q against report lines (whole line or a same-length window of it)."""
    import difflib
    nq = _norm_q(q)
    best = (0.0, "", None)
    pos = 0
    for line in report.split("\n"):
        a, pos = pos, pos + len(line) + 1
        nl = _norm_q(re.sub(r"^\s*\d+[.)]\s*", "", line))      # "1. Hepatomegaly": the list number isn't content
        if not nl:
            continue
        cands = [nl]
        words = nl.split()
        width = len(nq.split())
        if len(words) > width:
            cands += [" ".join(words[i:i + width + k]) for i in range(len(words) - width + 1) for k in (-1, 0, 1)
                      if width + k > 0]
        for c in cands:
            r = difflib.SequenceMatcher(None, nq, c).ratio()
            if r > best[0]:
                lead = len(line) - len(line.lstrip())
                best = (r, c, Span(start=a + lead, end=a + lead + len(line.strip()), text=line.strip()))
    return best


def _meaning_changed(q: str, rep: str) -> bool:
    nums = lambda x: set(re.findall(r"\d+(?:\.\d+)?", x))  # noqa: E731
    neg = lambda x: bool(re.search(r"\b(?:no|not|cannot|without|absent|negative)\b", x))  # noqa: E731
    from app.classify import find_hedges
    return nums(q) != nums(rep) or neg(q) != neg(rep) or (bool(find_hedges(rep)) and not find_hedges(q))


def _check_quote(c: Claim, analysis: AnalysisResult) -> Optional[Finding]:
    ratio, matched, rep_span = _best_quote_match(c.detail or "", analysis.text)
    if ratio >= 0.7 and _meaning_changed(_norm_q(c.detail or ""), matched):   # a changed number, negation or hedge
        return Finding(kind="quote", label="CONTRADICTED", ai_span=c.span, report_span=rep_span,
                       message=f"Misquotes your report: it says '{rep_span.text if rep_span else matched}'.")
    if ratio >= 0.85:
        return Finding(kind="quote", label="VERIFIED", ai_span=c.span, report_span=rep_span,
                       message="Quotes your report accurately.")
    return None


def _report_hedged_findings(analysis: AnalysisResult) -> dict[str, tuple[str, Span]]:
    """finding family -> (the report's hedged phrase, its line) for findings the report states with a hedge."""
    from app.classify import find_hedges
    from app.verify import lexicon as L
    gl = load_glossary()
    out: dict[str, tuple[str, Span]] = {}
    pos = 0
    for line in analysis.text.split("\n"):
        a, pos = pos, pos + len(line) + 1
        hedges = list(find_hedges(line))
        if not hedges and "?" not in line:
            continue
        for t in find_terms(line):
            if gl.get(t.term_id, {}).get("kind") != "finding":
                continue
            h = min(hedges, key=lambda m: abs(m.start() - t.span.start)) if hedges else None
            if h is not None and h.start() < t.span.start:
                phrase = line[h.start():t.span.end]
            else:
                phrase = line[max(0, t.span.start - 2):t.span.end].strip(" ;,")
                q = line.rfind("?", 0, t.span.start + 1)
                phrase = line[q:t.span.end] if q != -1 and t.span.start - q < 12 else line[t.span.start:t.span.end + 40].split(";")[0]
            lead = len(line) - len(line.lstrip())
            span = Span(start=a + lead, end=a + lead + len(line.strip()), text=line.strip())
            for fam in _families(t.term_id, L.FINDING_SYNONYMS):
                out.setdefault(fam, (" ".join(phrase.split()), span))
    return out


def _families(tid: str, synonyms) -> list[str]:
    for group in synonyms:
        if tid in group:
            return [group[0]]
    return [tid]


def _transparent(s: VerifiedSentence) -> bool:
    """A NEUTRAL line with no claims that only frames the next lines: short, a question, or ending in ':'."""
    if s.label != "NEUTRAL" or any(f.kind != "mention" for f in s.findings):
        return False
    t = s.text.strip()
    return t.endswith((":", "?")) or len(t.split()) <= 10


def _merge_overstep_passages(sentences: list[VerifiedSentence], list_ids: list[Optional[int]],
                             section_ids: list[int], ai_text: str) -> None:
    """Noise control: consecutive OVERSTEPS sentences in one section - items of one list, or prose separated only
    by framing lines (a sub-question, "Possible symptoms include:", a short bullet) - become one passage with one
    message. Each sentence keeps its own label; a sentence with checked facts breaks a passage."""
    i = 0
    while i < len(sentences):
        if sentences[i].label != "OVERSTEPS":
            i += 1
            continue
        members, j, k = [i], i, i + 1
        while k < len(sentences) and section_ids[k] == section_ids[i]:
            if sentences[k].label == "OVERSTEPS":
                members.append(k)
                j = k
            elif not _transparent(sentences[k]):
                break
            k += 1
        if len(members) > 1:
            n, pid = len(members), sentences[i].id
            for m in members:
                sentences[m].passage_id, sentences[m].passage_size = pid, n
            sentences[i].findings.insert(0, Finding(
                kind="passage", label="OVERSTEPS",
                ai_span=Span(start=sentences[i].span.start, end=sentences[j].span.end,
                             text=ai_text[sentences[i].span.start:sentences[j].span.end]),
                message=f"These {n} lines list possible causes, symptoms, outcomes or advice. Together they interpret "
                        "your results, which only your doctor can do."))
        i = j + 1


def verify(report_text: str, ai_text: str) -> VerifyResult:
    from app.parser.lines import normalize_text
    ai_text = normalize_text(ai_text)
    analysis = analyze(report_text)
    rtext = analysis.text
    idx = _index(analysis)
    hedged = _report_hedged_findings(analysis)
    age_txt = (analysis.structured_summary.report.get("age") or "") if analysis.structured_summary else ""
    patient_age = float(age_txt.split()[0]) if age_txt[:1].isdigit() else None
    sentences: list[VerifiedSentence] = []
    list_ids: list[Optional[int]] = []
    section_ids: list[int] = []
    mentioned: set[str] = set()
    for sc in extract(ai_text):
        findings: list[Finding] = []
        # value claims first: a status in the same segment is judged against the row whose value matched
        matched_row: dict[tuple[int, int], LabValue] = {}
        pairs: list[tuple[Claim, Finding]] = []
        matched_quotes: list[Span] = []
        # quotes are checked first: an overstep phrase inside an accurate quote is the report speaking
        for c in sorted(sc.claims, key=lambda c: (c.kind != "quote", c.kind != "value")):
            if c.kind == "group":
                findings.append(_check_group(c, analysis, idx))
                continue
            if c.kind == "quote":
                qf = _check_quote(c, analysis)
                if qf is not None:
                    findings.append(qf)
                    if qf.label == "VERIFIED":
                        matched_quotes.append(c.span)
                continue
            if c.kind == "certainty":
                from app.verify import lexicon as L
                for tid in (c.detail or "").split(","):
                    hit = next((hedged[f] for f in _families(tid, L.FINDING_SYNONYMS) if f in hedged), None)
                    if hit:
                        findings.append(Finding(kind="certainty", label="OVERSTEPS", ai_span=c.span, report_span=hit[1],
                                                message=f"Your report says '{hit[0]}'; the AI states it as certain."))
                        break
                continue
            if c.kind == "outside_range":
                findings.append(Finding(kind="outside_range", label="RANGE_NOT_FROM_REPORT", ai_span=c.span,
                                        message=f"The AI says it is using ranges that are not printed on your report "
                                                f"('{c.detail}'). MedLens only uses the ranges your report prints."))
                continue
            if c.anchor_value is not None:      # status bound by a bold value: the one report row with that value
                hits = [v for v in analysis.values if v.value_num is not None and _same(v.value_num, c.anchor_value)]
                if len(hits) == 1:
                    findings.append(_check_claim(c, rtext, hits, hits[0]))
                continue
            if c.kind == "range" and c.category and not c.term_id:
                findings.append(_check_category(c, rtext, []))
                continue
            rows = _rows_for(c.term_id, idx, analysis.values, c.mention.text if c.mention else "") if c.term_id else []
            key = (c.mention.start, c.mention.end) if c.mention else (-1, -1)
            f = _check_claim(c, rtext, rows, matched_row.get(key) if c.kind == "status" else None)
            if c.kind == "value" and f.label == "VERIFIED":
                matched_row[key] = next(v for v in rows if v.id == f.report_value_id)
            findings.append(f)
            pairs.append((c, f))
        _multi_range_assumptions(pairs, findings, analysis, patient_age, sc.span.text)
        if matched_quotes:
            def in_quote(f: Finding) -> bool:
                if any(q.end - q.start >= 0.8 * (sc.span.end - sc.span.start) for q in matched_quotes):
                    return True
                phrase = f.message.split("('")[1].split("')")[0].lower() if "('" in f.message else ""
                return bool(phrase) and any(phrase in q.text.lower() for q in matched_quotes)
            findings = [f for f in findings if not (f.kind in ("overstep", "certainty") and in_quote(f))]
        claimed = {(c.mention.start, c.mention.end) for c in sc.claims if c.mention}
        for m in sc.mentions:
            mentioned.add(m.term_id)
            mentioned.update(RELATED_TESTS.get(m.term_id, ()))
            if (m.span.start, m.span.end) not in claimed:
                rows = _rows_for(m.term_id, idx, analysis.values, m.span.text)
                if rows:      # a bare mention still links the sentence to the report line (not judged)
                    findings.append(Finding(kind="mention", label="NEUTRAL", term=m.term, ai_span=m.span,
                                            report_span=_line_span(rtext, rows[0].value_span.start),
                                            report_value_id=rows[0].id,
                                            message=f"Mentions {m.term}; no checkable claim about it."))
        judged = [f.label for f in findings if f.kind != "mention"]
        label = min(judged, key=SEVERITY.index) if judged else "NEUTRAL"
        sentences.append(VerifiedSentence(id=f"s{len(sentences)}", text=sc.span.text, span=sc.span, label=label,
                                          findings=findings))
        list_ids.append(sc.list_id)
        section_ids.append(sc.section_id)
    _merge_overstep_passages(sentences, list_ids, section_ids, ai_text)

    omissions = []
    lower_text = ai_text.lower()
    for v in analysis.values:
        if v.status not in OUT_OF_RANGE:
            continue
        ids = {t for t, rows in idx.items() if v in rows}
        if ids & mentioned or v.test_name.lower() in lower_text:
            continue
        omissions.append(Omission(
            value_id=v.id, test_name=v.test_name, value_raw=v.value_raw, status=v.status,
            report_span=_line_span(rtext, v.value_span.start),
            message=f"The AI didn't mention {v.test_name}, which is {STATUS_WORD[v.status]} the range printed "
                    f"on your report."))
    counts = {lab: 0 for lab in SEVERITY}
    for s in sentences:
        for f in s.findings:
            if f.kind not in ("mention", "passage"):
                counts[f.label] += 1
    counts["OMISSION"] = len(omissions)
    # what the summary bar counts: one per OVERSTEPS passage (a merged list counts once)
    counts["OVERSTEP_PASSAGES"] = len({s.passage_id or s.id for s in sentences if s.label == "OVERSTEPS"})
    claims_checked = sum(n for k, n in counts.items() if k not in ("NEUTRAL", "OMISSION"))
    return VerifyResult(sentences=sentences, omissions=omissions, counts=counts, claims_checked=claims_checked,
                        report_analysis=analysis)
