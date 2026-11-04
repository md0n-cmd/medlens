"""Pydantic schemas shared by the parser, pipeline and API."""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Span(BaseModel):
    """Character offsets into the (normalised) report text. end is exclusive."""
    start: int
    end: int
    text: str


class Status(str, Enum):
    WITHIN = "within_range"
    ABOVE = "above_range"
    BELOW = "below_range"
    DIFFERS = "differs_from_reference"          # qualitative mismatch (e.g. Positive vs Negative)
    NO_RANGE = "no_reference_range_supplied"
    AMBIGUOUS = "ambiguous_range"
    UNIT_MISMATCH = "unit_mismatch"
    INDETERMINATE = "indeterminate"
    UNPARSEABLE = "unparseable"


OUT_OF_RANGE = {Status.ABOVE, Status.BELOW, Status.DIFFERS}


class Tag(str, Enum):
    FACTUAL = "FACTUAL_FROM_REPORT"
    GENERAL = "GENERAL_INFO"
    PROFESSIONAL = "NEEDS_PROFESSIONAL_INTERPRETATION"


RangeKind = Literal["interval", "upper", "lower", "qualitative", "multi", "unparseable"]


class LabeledRange(BaseModel):
    label: str                      # e.g. "M", "Female", "Adult"
    range: "ReferenceRange"


class ReferenceRange(BaseModel):
    raw: str
    kind: RangeKind
    low: Optional[float] = None
    high: Optional[float] = None
    low_inclusive: bool = True
    high_inclusive: bool = True
    unit: Optional[str] = None
    qualitative: Optional[str] = None
    alternatives: list[LabeledRange] = Field(default_factory=list)

    def display(self) -> str:
        """Human readable form, built only from what the report supplied."""
        u = f" {self.unit}" if self.unit else ""
        if self.kind == "interval":
            return f"{_fmt(self.low)}–{_fmt(self.high)}{u}"
        if self.kind == "upper":
            return f"{'≤' if self.high_inclusive else '<'} {_fmt(self.high)}{u}"
        if self.kind == "lower":
            return f"{'≥' if self.low_inclusive else '>'} {_fmt(self.low)}{u}"
        if self.kind == "qualitative":
            return self.qualitative or self.raw
        if self.kind == "multi":
            return "; ".join(f"{a.label}: {a.range.display()}" for a in self.alternatives)
        return self.raw


def _fmt(x: Optional[float]) -> str:
    if x is None:
        return "?"
    return f"{x:g}" if abs(x) < 1e6 else f"{x:,.0f}"


class LabValue(BaseModel):
    id: str
    line_number: int
    section: Optional[str] = None
    test_name: str
    test_span: Span
    value_raw: str
    value_num: Optional[float] = None
    value_comparator: Optional[str] = None       # '<', '<=', '>', '>=' for censored values
    value_high: Optional[float] = None           # upper end for range-valued results ("6-8 /hpf")
    value_qualitative: Optional[str] = None
    value_span: Span
    unit: Optional[str] = None
    unit_span: Optional[Span] = None
    flag_raw: Optional[str] = None               # flag printed by the lab (H, L, *, ↑ ...)
    flag_span: Optional[Span] = None
    reference: Optional[ReferenceRange] = None
    reference_span: Optional[Span] = None
    applied_range: Optional[ReferenceRange] = None   # the range actually used (for multi ranges picked by sex)
    status: Status = Status.NO_RANGE
    status_explanation: str = ""
    flag_disagreement: bool = False
    missing_range_reason: Optional[str] = None
    notes: list[str] = Field(default_factory=list)
    glossary_term_id: Optional[str] = None


class TermMatch(BaseModel):
    id: str
    term_id: str
    term: str
    matched_text: str
    span: Span
    category: str
    plain_explanation: str
    what_it_is_not: Optional[str] = None
    source_note: Optional[str] = None
    ambiguous: bool = False
    possibilities: list[str] = Field(default_factory=list)


class UnrecognizedTerm(BaseModel):
    id: str
    text: str
    span: Span
    reason: str


class ExplanationItem(BaseModel):
    id: str
    tag: Tag
    title: str
    text: str
    source_snippet: str
    span: Optional[Span] = None
    passage: Optional[Span] = None               # whole source line, or whole section block for Impression etc.
    related_id: Optional[str] = None             # id of the LabValue / TermMatch / etc.
    origin: Literal["rule", "glossary", "llm"] = "rule"
    safety_filtered: bool = False


class Section(BaseModel):
    name: str
    start: int
    end: int
    interpretive: bool = False


class Summary(BaseModel):
    terms_found: int = 0
    values_parsed: int = 0
    out_of_range: int = 0
    within_range: int = 0
    ambiguous: int = 0
    no_range_supplied: int = 0
    other_status: int = 0
    flag_disagreements: int = 0
    unrecognized_terms: int = 0
    needs_professional: int = 0


class SummaryTest(BaseModel):
    name: str
    value: str
    unit: Optional[str] = None
    printed_range: Optional[str] = None
    status: Status
    lab_flag: Optional[str] = None
    passage_span: Span


class SummarySection(BaseModel):
    title: str
    tests: list[SummaryTest] = Field(default_factory=list)


class SummaryLine(BaseModel):
    text: str
    reason: str
    passage_span: Span


class OutsideItem(BaseModel):
    name: str
    value: str
    direction: Literal["higher", "lower", "differs"]
    printed_range: Optional[str] = None


class MissingItem(BaseModel):
    name: str
    value: str
    reason: str


class StructuredSummary(BaseModel):
    """Built ONLY from facts already extracted. Never contains the patient's name."""
    report: dict[str, Optional[str]]
    sections: list[SummarySection]
    counts: dict[str, int]
    outside_printed_range: list[OutsideItem]
    missing_range: list[MissingItem]
    needs_professional_interpretation: list[SummaryLine]
    unrecognized_terms: list[str]


class AnalyzeRequest(BaseModel):
    text: str = Field(..., max_length=100_000)


class AnalysisResult(BaseModel):
    text: str
    patient_sex: Optional[str] = None
    sections: list[Section]
    values: list[LabValue]
    terms: list[TermMatch]
    unrecognized: list[UnrecognizedTerm]
    explanations: list[ExplanationItem]
    summary: Summary
    disclaimer: str
    warnings: list[str] = Field(default_factory=list)
    structured_summary: Optional[StructuredSummary] = None


LabeledRange.model_rebuild()
