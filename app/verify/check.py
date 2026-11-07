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
from app.glossary.matcher import load_glossary
from app.verify.claims import Claim, extract
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
                                   "your doctor can make."}

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
    kind: Literal["value", "range", "status", "overstep", "mention"]
    label: Label
    message: str
    term: Optional[str] = None
    ai_span: Span
    report_span: Optional[Span] = None
    report_value_id: Optional[str] = None


class VerifiedSentence(BaseModel):
    id: str
    text: str
    span: Span
    label: Label
    findings: list[Finding] = Field(default_factory=list)


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
    return {Status.ABOVE: "HIGH", Status.BELOW: "LOW", Status.WITHIN: "NORMAL"}.get(v.status)


def _unsettled_reason(v: LabValue) -> str:
    if v.status == Status.AMBIGUOUS:
        return "the report lists more than one range for this test and doesn't say which applies"
    if v.status in (Status.NO_RANGE, Status.UNPARSEABLE, Status.UNIT_MISMATCH):
        return "the report prints no usable range for this test"
    return "the comparison with the printed range is not clear-cut"


# ------------------------------------------------------------------ checking
def _check_claim(c: Claim, text: str, rows: list[LabValue], value_row: Optional[LabValue]) -> Finding:
    span = c.span
    if c.kind == "overstep":
        phrase = c.detail if c.detail and c.detail.lower() in span.text.lower() else ""
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
    if rs == c.status:
        return Finding(kind="status", label="VERIFIED", term=c.term, ai_span=span, report_span=rep_line(v),
                       report_value_id=v.id,
                       message=f"Matches: {v.value_raw} is {STATUS_WORD[v.status]} the range printed on your report ({rng}).")
    return Finding(kind="status", label="CONTRADICTED", term=c.term, ai_span=span, report_span=rep_line(v),
                   report_value_id=v.id,
                   message=f"AI says {CLAIM_WORD[c.status]}; compared with the range printed on your report ({rng}), "
                           f"{v.value_raw} is {STATUS_WORD[v.status]} it.")


def verify(report_text: str, ai_text: str) -> VerifyResult:
    from app.parser.lines import normalize_text
    ai_text = normalize_text(ai_text)
    analysis = analyze(report_text)
    rtext = analysis.text
    idx = _index(analysis)
    sentences: list[VerifiedSentence] = []
    mentioned: set[str] = set()
    for sc in extract(ai_text):
        findings: list[Finding] = []
        # value claims first: a status in the same segment is judged against the row whose value matched
        matched_row: dict[tuple[int, int], LabValue] = {}
        for c in sorted(sc.claims, key=lambda c: c.kind != "value"):
            rows = _rows_for(c.term_id, idx, analysis.values, c.mention.text if c.mention else "") if c.term_id else []
            key = (c.mention.start, c.mention.end) if c.mention else (-1, -1)
            f = _check_claim(c, rtext, rows, matched_row.get(key) if c.kind == "status" else None)
            if c.kind == "value" and f.label == "VERIFIED":
                matched_row[key] = next(v for v in rows if v.id == f.report_value_id)
            findings.append(f)
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
            if f.kind != "mention":
                counts[f.label] += 1
    counts["OMISSION"] = len(omissions)
    claims_checked = sum(n for k, n in counts.items() if k not in ("NEUTRAL", "OMISSION"))
    return VerifyResult(sentences=sentences, omissions=omissions, counts=counts, claims_checked=claims_checked,
                        report_analysis=analysis)
