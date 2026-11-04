"""Structured report summary: a re-arrangement of facts already extracted. No new interpretation."""
from __future__ import annotations

import re
from typing import Optional

from app.models import (AnalysisResult, MissingItem, OutsideItem, Span, Status, StructuredSummary, SummaryLine,
                        SummarySection, SummaryTest, Tag)

DATE_RE = re.compile(r"(?:reported\s+on|report\s+date|collected\s+on|date)\s*[:\-]\s*(?P<d>\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})", re.I)
SPECIMEN_RE = re.compile(r"(?:sample|specimen)(?:\s+type)?\s*:\s*(?P<s>[A-Za-z][A-Za-z ]*?)(?:\s{2,}|$)", re.I | re.M)
STUDY_RE = re.compile(r"^\s*study\s*:\s*(?P<s>.+?)\s*$", re.I | re.M)
NOT_LAB = re.compile(r"patient|name|age|sex|ref\.?|report|sample|study|date|\*\*\*", re.I)


def _lab_name(text: str) -> Optional[str]:
    for line in text.split("\n")[:6]:
        s = line.strip()
        if s and not NOT_LAB.search(s) and ":" not in s:
            return s
    return None


def _line_span(text: str, start: int) -> Span:
    a = text.rfind("\n", 0, start) + 1
    b = text.find("\n", start)
    b = len(text) if b == -1 else b
    lead = len(text[a:b]) - len(text[a:b].lstrip())
    seg = text[a:b].strip()
    return Span(start=a + lead, end=a + lead + len(seg), text=seg)


def build_summary(r: AnalysisResult, patient_age: Optional[float]) -> StructuredSummary:
    head = "\n".join(r.text.split("\n")[:25])
    dm, sm, st = DATE_RE.search(head), SPECIMEN_RE.search(head), STUDY_RE.search(head)
    report = {
        "lab": _lab_name(r.text),
        "date": dm["d"] if dm else None,
        "specimen": sm["s"].strip() if sm else (st["s"] if st else None),
        "age": f"{patient_age:g} Y" if patient_age is not None else None,
        "sex": r.patient_sex.title() if r.patient_sex else None,
    }
    sections: list[SummarySection] = []
    for v in r.values:
        title = v.section or "Results"
        if not sections or sections[-1].title != title:
            sections.append(SummarySection(title=title))
        sections[-1].tests.append(SummaryTest(
            name=v.test_name, value=v.value_raw, unit=v.unit,
            printed_range=v.reference_span.text if v.reference_span else None, status=v.status,
            lab_flag=v.flag_raw, passage_span=_line_span(r.text, v.value_span.start)))
    for sec in r.sections:                       # narrative reports: keep the section skeleton
        if not any(s.title == sec.name for s in sections):
            sections.append(SummarySection(title=sec.name))
    order = {s.name: s.start for s in r.sections}
    sections.sort(key=lambda s: order.get(s.title, s.tests[0].passage_span.start if s.tests else 0))

    counts = {s.value: 0 for s in Status}
    for v in r.values:
        counts[v.status.value] += 1
    direction = {Status.ABOVE: "higher", Status.BELOW: "lower", Status.DIFFERS: "differs"}
    outside = [OutsideItem(name=v.test_name, value=v.value_raw, direction=direction[v.status],
                           printed_range=v.reference_span.text if v.reference_span else None)
               for v in r.values if v.status in direction]
    missing = [MissingItem(name=v.test_name, value=v.value_raw, reason=v.missing_range_reason or "no_range_printed")
               for v in r.values if getattr(v, "missing_range_reason", None) or v.status == Status.NO_RANGE]
    needs = [SummaryLine(text=e.source_snippet, reason=e.title, passage_span=e.span)
             for e in r.explanations
             if e.tag == Tag.PROFESSIONAL and e.related_id is None and e.span is not None]
    return StructuredSummary(report=report, sections=sections, counts=counts, outside_printed_range=outside,
                             missing_range=missing, needs_professional_interpretation=needs,
                             unrecognized_terms=[u.text for u in r.unrecognized])
