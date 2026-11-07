"""Narrative findings (imaging / ECG reports): each mention of a report concept with its polarity and a
word-level hedge. "Right lower zone opacity, likely infiltrate": the opacity is stated, only the infiltrate
is hedged. Used to say an AI sentence "matches your report" and to scope the certainty check."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.glossary.matcher import find_terms, load_glossary
from app.models import Span
from app.verify import lexicon as L

CLAUSE_END = re.compile(r"[;,:|]|\.\s|\.$|\s(?:but|whereas|although|however|which|that)\s", re.I)


def _alt(items) -> str:
    return "(?:" + "|".join(items) + ")"


NORMAL_RE = re.compile(rf"(?<!than\s)\b{_alt(L.FINDING_NORMAL)}\b", re.I)
ABNORMAL_RE = re.compile(rf"\b{_alt(L.FINDING_ABNORMAL)}\b", re.I)
NEG_RE = re.compile(rf"(?:^|\b){_alt(L.FINDING_NEGATION)}\b", re.I)
HEDGE_BEFORE = re.compile(rf"(?:\b|(?=\?)){_alt(L.FINDING_HEDGE_BEFORE)}(?:\b|(?<=\?))(?:\s+\S+){{0,3}}\s*$", re.I)
HEDGE_AFTER = re.compile(rf"^\W*(?:\S+\s+){{0,1}}{_alt(L.FINDING_HEDGE_AFTER)}", re.I)
LAY = [(cid, re.compile(rf"\b{p}\b", re.I)) for cid, pats in L.LAY_FINDINGS.items() for p in pats]


@dataclass
class FindingMention:
    concept: str            # family id (FINDING_SYNONYMS collapse to their first member)
    start: int
    end: int
    pathology: bool         # a finding (present / absent) vs a structure (normal / abnormal)
    polarity: str           # "present" | "absent" | "normal" | "abnormal" | "" (structure, nothing said)
    hedged: bool


def _family(cid: str) -> str:
    for group in L.FINDING_SYNONYMS:
        if cid in group:
            return group[0]
    return cid


def _clause(text: str, a: int, b: int) -> tuple[int, int]:
    lo = 0
    for m in CLAUSE_END.finditer(text, 0, a):
        lo = m.end()
    m = CLAUSE_END.search(text, b)
    hi = m.start() if m else len(text)
    return lo, hi


def mentions(text: str) -> list[FindingMention]:
    gl = load_glossary()
    raw: list[tuple[int, int, str, bool]] = []
    for t in find_terms(text):
        e = gl.get(t.term_id, {})
        if e.get("kind") == "finding":
            raw.append((t.span.start, t.span.end, t.term_id, True))
        elif e.get("kind") == "report_word" and t.term_id not in L.FINDING_QUALIFIERS:
            raw.append((t.span.start, t.span.end, t.term_id, False))
    for cid, rx in LAY:
        for m in rx.finditer(text):
            raw.append((m.start(), m.end(), cid, gl.get(cid, {}).get("kind") == "finding"))
    raw.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    out: list[FindingMention] = []
    for a, b, cid, path in raw:
        if out and a < out[-1].end:
            continue                                  # overlapping: the longer, earlier match wins
        lo, hi = _clause(text, a, b)
        before, after = text[lo:a], text[b:hi]
        # what describes the structure is the clause minus every concept name in it
        rest = before + " " + after
        hedged = bool(HEDGE_BEFORE.search(before) or HEDGE_AFTER.match(text[b:min(len(text), b + 40)]))
        if path:
            pol = "absent" if NEG_RE.search(before) else "present"
        elif NORMAL_RE.search(rest):
            pol = "normal"
        elif ABNORMAL_RE.search(rest) or ABNORMAL_RE.search(text[a:b]):
            pol = "abnormal"
        else:
            pol = ""
        out.append(FindingMention(_family(cid), a, b, path, pol, hedged))
    return out


@dataclass
class ReportFinding:
    polarity: str
    hedged: bool
    span: Span


def report_findings(report_text: str) -> dict[str, list[ReportFinding]]:
    out: dict[str, list[ReportFinding]] = {}
    pos = 0
    for line in report_text.split("\n"):
        a, pos = pos, pos + len(line) + 1
        if not line.strip():
            continue
        lead = len(line) - len(line.lstrip())
        span = Span(start=a + lead, end=a + lead + len(line.strip()), text=line.strip())
        for m in mentions(line):
            if m.polarity:
                out.setdefault(m.concept, []).append(ReportFinding(m.polarity, m.hedged, span))
    return out


def stated_unhedged(rep: dict[str, list[ReportFinding]], concept: str) -> bool:
    return any(f.polarity == "present" and not f.hedged for f in rep.get(concept, []))


LABEL_RE = re.compile(r"^\W*(?:\d+[.)]\s*)?(?:\*\*)?(?P<label>[^:.!?]{2,60}?)(?:\*\*)?\s*:(?:\*\*)?\s")


@dataclass
class Match:
    ok: bool
    matched: list[tuple[FindingMention, Optional[Span]]]


def match_sentence(sentence: str, rep: dict[str, list[ReportFinding]]) -> Match:
    """Every finding the sentence states matches a report finding with the same polarity and the same or
    weaker certainty. A bullet's lead label ("Right lower zone opacity / Infiltrate:") only names the topic:
    it must be in the report, its certainty is not checked."""
    lm = LABEL_RE.match(sentence)
    label_end = lm.end("label") if lm and len(lm["label"].split()) <= 8 else -1
    matched: list[tuple[FindingMention, Optional[Span]]] = []
    for m in mentions(sentence):
        entries = rep.get(m.concept, [])
        if m.end <= label_end:
            if not entries:
                return Match(False, [])
            matched.append((m, entries[0].span))
            continue
        if not m.polarity:
            continue                                   # a structure named without saying anything about it
        agree = [f for f in entries if f.polarity == m.polarity or
                 {f.polarity, m.polarity} <= {"absent", "normal"}]
        if not agree:
            return Match(False, [])
        if m.polarity == "present" and not m.hedged:
            agree = [f for f in agree if not f.hedged]  # stated as certain: the report must state it too
            if not agree:
                return Match(False, [])
        matched.append((m, agree[0].span))
    return Match(bool(matched), matched)
