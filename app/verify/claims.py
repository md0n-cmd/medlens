"""Claim extraction from an AI-written explanation of a report (rule-based, no LLM).

For every sentence we find:
  * test mentions   - glossary terms of kind "test" (same matcher, longest match, same case rules)
  * value claims    - a mention followed by a number ("hemoglobin is 11.2 g/dL", "Hb of 12", "2.1 lakh")
  * range claims    - a mention followed by a range ("normal range is 13-17", "should be below 200")
  * status claims   - a mention followed by status words (low / high / normal ...)
  * oversteps       - diagnosis, treatment/dosage or false-reassurance statements (safety.py + extra rules)
Each claim is attached to the text after its mention, up to the next mention (its "segment").
Offsets are absolute character positions in the AI text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from app.glossary.matcher import find_terms, load_glossary
from app.models import ReferenceRange, Span
from app.parser.ranges import NUM, parse_number, parse_range
from app.safety import violations

N = f"(?:{NUM})"
MULT = r"(?:\s*(?P<mult>lakhs?|lacs?|thousand|k)\b)?"
MULTIPLIER = {"lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "thousand": 1e3, "k": 1e3}

# ------------------------------------------------------------------ sentences
def split_sentences(text: str) -> list[Span]:
    """Sentence spans; a '.' inside a number (11.2) never ends a sentence."""
    out, start = [], 0
    for m in re.finditer(r"(?<!\d)[.!?]+(?=\s|$)|(?<=\d)[.!?]+(?=\s+[A-Z(\"']|\s*$)|\n+", text):
        end = m.end()
        seg = text[start:end]
        lead = len(seg) - len(seg.lstrip())
        s = seg.strip()
        if s and re.search(r"[A-Za-z0-9]", s):
            out.append(Span(start=start + lead, end=start + lead + len(s), text=s))
        start = end
    seg = text[start:]
    if seg.strip() and re.search(r"[A-Za-z0-9]", seg):
        lead = len(seg) - len(seg.lstrip())
        s = seg.strip()
        out.append(Span(start=start + lead, end=start + lead + len(s), text=s))
    return out


# ------------------------------------------------------------------ claim types
Status = Literal["LOW", "HIGH", "NORMAL"]


@dataclass
class Claim:
    kind: Literal["value", "range", "status", "overstep"]
    span: Span                                   # the claim text in the AI explanation
    term_id: Optional[str] = None
    term: Optional[str] = None
    mention: Optional[Span] = None
    value: Optional[float] = None
    value_raw: Optional[str] = None
    value_high: Optional[float] = None           # "6-8 /hpf"
    unit: Optional[str] = None
    range: Optional[ReferenceRange] = None
    status: Optional[Status] = None
    overstep: Optional[str] = None               # "diagnosis" | "treatment" | "reassurance"
    detail: Optional[str] = None


@dataclass
class Mention:
    term_id: str
    term: str
    span: Span


@dataclass
class SentenceClaims:
    span: Span
    mentions: list[Mention] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)


# ------------------------------------------------------------------ patterns
RANGE_CUE = re.compile(r"\b(?:range|normal|reference|between|should|ideal|healthy|limits?|interval|expected|"
                       r"typical|usual|standard|optimal|desirable|target)\b", re.I)
INTERVAL_RE = re.compile(rf"(?:between\s+)?(?P<lo>{N})\s*(?:-|–|—|to|and)\s*(?P<hi>{N})", re.I)
UPPER_RE = re.compile(rf"\b(?P<op>below|under|less\s+than|lower\s+than|up\s*to|not\s+more\s+than|no\s+more\s+than|"
                      rf"max(?:imum)?(?:\s+of)?)\s*(?P<n>{N})|(?P<sym><=|≤|<)\s*(?P<n2>{N})", re.I)
LOWER_RE = re.compile(rf"\b(?P<op>above|over|more\s+than|greater\s+than|higher\s+than|at\s+least|"
                      rf"min(?:imum)?(?:\s+of)?)\s*(?P<n>{N})|(?P<sym>>=|≥|>)\s*(?P<n2>{N})", re.I)
VALUE_RE = re.compile(rf"(?<![\w.,])(?P<cmp><=|>=|≤|≥|<|>)?\s*(?P<num>{N}){MULT}"
                      r"(?:\s*(?P<unit>(?:x\s*10\^?\d+\s*)?(?:[µμu]?[A-Za-z%]+(?:/[A-Za-z0-9.\^]+)*|/[A-Za-z]+)))?", re.I)
UNIT_WORDS = re.compile(r"^(?:g/dl|mg/dl|mg/l|ng/ml|pg/ml|ug/dl|µg/dl|u/l|iu/l|miu/l|µiu/ml|uiu/ml|mmol/l|meq/l|fl|pg|%|"
                        r"/cumm|/hpf|cells/cumm|million/cumm|mm/hr|mm|sec|seconds|min|ms|bpm|x10\^\d+/[a-z]+|10\^\d+/[a-z]+|"
                        r"ml/min(?:/1\.73m2)?|lakhs?|thousand|k)$", re.I)

LOW_RE = re.compile(r"\b(?:low|lower|below\s+(?:the\s+)?(?:normal|reference|printed|range|limit)|decreased|deficient|"
                    r"deficiency|reduced|on\s+the\s+lower\s+side)\b", re.I)
HIGH_RE = re.compile(r"\b(?:high|higher|above\s+(?:the\s+)?(?:normal|reference|printed|range|limit)|elevated|raised|"
                     r"increased|on\s+the\s+higher\s+side)\b", re.I)
NORMAL_RE = re.compile(r"\b(?:within\s+(?:the\s+)?(?:normal\s+|reference\s+|printed\s+)?(?:ranges?|limits)|"
                       r"in\s+(?:the\s+)?(?:normal\s+|reference\s+)?ranges?|normal(?!\s+(?:range|limits?|values?|levels?)\b)|"
                       r"fine|healthy|okay|ok)\b", re.I)
NEGATION = re.compile(r"\b(?:not|isn't|aren't|wasn't|n't|never|no)\s+(?:\w+\s+){0,2}$", re.I)

DISEASE = (r"(?:an?\s+|the\s+)?(?:mild\s+|early\s+|severe\s+|some\s+)?(?:[a-z]+\s+){0,2}(?:anaemia|anemia|diabetes|pre-?diabetes|infection|"
           r"disease|disorder|deficiency|cancer|tumou?r|failure|damage|hypothyroidism|hyperthyroidism|thyroid\s+problem|"
           r"\w+(?:itis|emia|aemia|osis|pathy))\b")
DIAG_EXTRA = [
    re.compile(r"\byou(?:'re|\s+are)\s+(?:probably\s+|likely\s+)?(?:suffering|anaemic|anemic|diabetic|pre-?diabetic|"
               r"hypothyroid|hyperthyroid)\b", re.I),
    re.compile(r"\b(?:this|these|it|which|that|they)\s+(?:results?\s+|values?\s+)?(?:clearly\s+|strongly\s+)?"
               r"(?:indicates?|confirms?|suggests?|means?|shows?|points?\s+to|proves?|is\s+a\s+sign\s+of|is\s+consistent\s+with)"
               r"\s+(?:that\s+)?(?:you\s+(?:have|are)\s+)?" + DISEASE, re.I),
    re.compile(r"\b(?:sign|signs|symptom|evidence)\s+of\s+" + DISEASE, re.I),
]
TREAT_EXTRA = [
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|iu|tablets?|tabs?|capsules?)(?![/\w])", re.I),
    re.compile(r"\b(?:once|twice|thrice|\d+\s+times)\s+(?:a|per|each)\s+day\b|\bevery\s+(?:morning|night|day)\b", re.I),
    re.compile(r"\b(?:should|must|need\s+to|have\s+to)\s+(?:start\s+|stop\s+|take\s+|begin\s+)?(?:taking\s+)?(?:\w+\s+){0,3}"
               r"(?:tablets?|supplements?|medicines?|medication|pills?|capsules?|injections?|antibiotics?)\b", re.I),
    re.compile(r"\b(?:take|start|begin)\s+(?:\w+\s+){0,2}(?:tablets?|supplements?|medicines?|medication|pills?|capsules?)\b", re.I),
]
REASSURE_EXTRA = [
    re.compile(r"\bno\s+need\s+to\s+(?:see|visit|consult|worry|meet)\b", re.I),
    re.compile(r"\bnothing\s+(?:at\s+all\s+)?to\s+worry\b", re.I),
    re.compile(r"\b(?:don't|do\s+not|dont)\s+(?:need\s+to\s+)?worry\b", re.I),
    re.compile(r"\bno\s+cause\s+for\s+(?:concern|alarm|worry)\b", re.I),
    re.compile(r"\byou(?:'re|\s+are|\s+will\s+be)\s+(?:perfectly\s+|completely\s+|totally\s+)?(?:fine|healthy|ok|okay)\b", re.I),
]
SAFETY_KIND = {
    "diagnostic claim about the user": "diagnosis", "diagnostic label for the user": "diagnosis",
    "states a diagnosis": "diagnosis", "interprets result for the user": "diagnosis",
    "false reassurance": "reassurance", "prognosis": "reassurance",
}


def find_oversteps(sentence: str) -> list[tuple[str, str]]:
    """[(category, reason)] for diagnosis / treatment / reassurance statements in one sentence."""
    out: list[tuple[str, str]] = []
    for why in violations(sentence):
        out.append((SAFETY_KIND.get(why, "treatment"), why))
    for rx in DIAG_EXTRA:
        if rx.search(sentence):
            out.append(("diagnosis", "states or implies a diagnosis"))
    for rx in TREAT_EXTRA:
        if rx.search(sentence):
            out.append(("treatment", "gives treatment or dosage advice"))
    for rx in REASSURE_EXTRA:
        if rx.search(sentence):
            out.append(("reassurance", "gives reassurance only a doctor can give"))
    seen, uniq = set(), []
    for cat, why in out:
        if cat not in seen:
            seen.add(cat)
            uniq.append((cat, why))
    return uniq


# ------------------------------------------------------------------ helpers
def _num(raw: str, mult: Optional[str]) -> float:
    return parse_number(raw) * MULTIPLIER.get((mult or "").lower(), 1.0)


def _test_mentions(text: str) -> list[Mention]:
    gl = load_glossary()
    out = []
    for t in find_terms(text):
        e = gl.get(t.term_id, {})
        if e.get("kind", "test") == "test" or e.get("possibilities"):     # ambiguous abbreviations like PCV too
            out.append(Mention(term_id=t.term_id, term=t.term, span=t.span))
    return out


def _sp(text: str, a: int, b: int) -> Span:
    return Span(start=a, end=b, text=text[a:b])


DOSE_AFTER = re.compile(r"\s*(?:mg|mcg|µg|iu|tablets?|tabs?|capsules?|drops?)(?![/\w])", re.I)


def _segment_claims(text: str, m: Mention, a: int, b: int, sent_start: int) -> list[Claim]:
    """Claims in text[a:b], the part of the sentence that belongs to mention m."""
    seg = text[a:b]
    claims: list[Claim] = []
    used: list[tuple[int, int]] = []

    def free(s: int, e: int) -> bool:
        return all(e <= us or s >= ue for us, ue in used)

    base = dict(term_id=m.term_id, term=m.term, mention=m.span)
    # ranges first (their numbers must not be read as values)
    for im in INTERVAL_RE.finditer(seg):
        before = text[sent_start:a + im.start()]
        in_paren = before.rstrip().endswith("(")
        if not (RANGE_CUE.search(before) or in_paren or im.group(0).lower().startswith("between")):
            continue
        r = parse_range(f"{im['lo']} - {im['hi']}")
        if r.kind != "unparseable":
            used.append((im.start(), im.end()))
            claims.append(Claim(kind="range", span=_sp(text, a + im.start(), a + im.end()), range=r, **base))
    for rx, kind in ((UPPER_RE, "upper"), (LOWER_RE, "lower")):
        for rm in rx.finditer(seg):
            if not free(rm.start(), rm.end()):
                continue
            n = rm["n"] or rm["n2"]
            if rm["sym"]:
                op = rm["sym"]
            else:
                op = re.sub(r"\s+", " ", rm["op"].lower())
            inclusive = op in ("up to", "upto", "<=", "≤", ">=", "≥", "at least", "not more than", "no more than") \
                or op.startswith("max") or op.startswith("min")
            r = ReferenceRange(raw=rm.group(0), kind=kind, high=parse_number(n) if kind == "upper" else None,
                               low=parse_number(n) if kind == "lower" else None,
                               high_inclusive=inclusive, low_inclusive=inclusive)
            used.append((rm.start(), rm.end()))
            claims.append(Claim(kind="range", span=_sp(text, a + rm.start(), a + rm.end()), range=r, **base))
    # value: first standalone number left over (a value written as a span, e.g. "6-8", counts too)
    for vm in VALUE_RE.finditer(seg):
        if not free(vm.start("num"), vm.end("num")):
            continue
        if DOSE_AFTER.match(seg, vm.end("num")):
            continue                              # "100 mg twice a day" is a dose, not a test value
        span_hi = re.match(rf"\s*[-–]\s*(?P<hi>{N})", seg[vm.end("num"):])
        if span_hi and not vm["mult"]:            # a value written as a span, e.g. pus cells "6-8 /hpf"
            hi_end = vm.end("num") + span_hi.end()
            um = re.match(r"\s*(?P<unit>/?[A-Za-z%/]+)", seg[hi_end:])
            end = hi_end + (um.end() if um and UNIT_WORDS.match(um["unit"]) else 0)
            claims.append(Claim(kind="value", span=_sp(text, a + vm.start("num"), a + end), value=parse_number(vm["num"]),
                                value_high=parse_number(span_hi["hi"]), value_raw=text[a + vm.start("num"):a + end].strip(),
                                unit=um["unit"] if um and UNIT_WORDS.match(um["unit"]) else None, **base))
            used.append((vm.start(), end))
            break
        unit = (vm["unit"] or "").rstrip(".") or None
        if unit and not UNIT_WORDS.match(unit.replace(" ", "")):
            unit = None
        end = vm.start("unit") + len(unit) if unit else (vm.end("mult") if vm["mult"] else vm.end("num"))
        raw = text[a + vm.start("num"):a + end] if not vm["cmp"] else text[a + vm.start():a + end]
        claims.append(Claim(kind="value", span=_sp(text, a + vm.start("num") if not vm["cmp"] else a + vm.start(), a + end),
                            value=_num(vm["num"], vm["mult"]), value_raw=raw.strip(), unit=unit, **base))
        used.append((vm.start(), end))
        break
    # status words (not inside a range phrase, not negated)
    for rx, st in ((LOW_RE, "LOW"), (HIGH_RE, "HIGH"), (NORMAL_RE, "NORMAL")):
        for sm in rx.finditer(seg):
            if not free(sm.start(), sm.end()) or NEGATION.search(seg[:sm.start()]):
                continue
            claims.append(Claim(kind="status", span=_sp(text, a + sm.start(), a + sm.end()), status=st, **base))
            break
    statuses = [c for c in claims if c.kind == "status"]
    if len(statuses) > 1:                         # keep the one written first
        first = min(statuses, key=lambda c: c.span.start)
        claims = [c for c in claims if c.kind != "status" or c is first]
    return claims


def extract(text: str) -> list[SentenceClaims]:
    mentions_all = _test_mentions(text)
    out: list[SentenceClaims] = []
    for sp in split_sentences(text):
        sc = SentenceClaims(span=sp)
        ms = [m for m in mentions_all if sp.start <= m.span.start and m.span.end <= sp.end]
        sc.mentions = ms
        per_mention: list[list[Claim]] = []
        for i, m in enumerate(ms):
            end = ms[i + 1].span.start if i + 1 < len(ms) else sp.end
            per_mention.append(_segment_claims(text, m, m.span.end, end, sp.start))
        # "Your MCV and MCH are normal": a mention followed only by a conjunction shares the next one's status
        for i in range(len(ms) - 2, -1, -1):
            gap = text[ms[i].span.end:ms[i + 1].span.start]
            if not per_mention[i] and re.fullmatch(r"[\s,/&]*(?:and|or|&)?[\s,/]*", gap, re.I):
                for c in per_mention[i + 1]:
                    if c.kind == "status":
                        per_mention[i].append(Claim(kind="status", span=c.span, status=c.status, term_id=ms[i].term_id,
                                                    term=ms[i].term, mention=ms[i].span))
        for cl in per_mention:
            sc.claims.extend(cl)
        for cat, why in find_oversteps(sp.text):
            sc.claims.append(Claim(kind="overstep", span=sp, overstep=cat, detail=why))
        out.append(sc)
    return out
