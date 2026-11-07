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
ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "no", "nos", "vs", "etc", "approx", "fig", "st", "jr", "sr", "dept", "ref",
                 "e.g", "i.e", "viz", "cf", "max", "min", "avg", "hr", "hrs", "yr", "yrs", "inc", "ltd", "pvt"}
# a standalone token: dotted abbreviation (E.S.R, i.e) or a single capital letter ("Grade A."), never "U/L"
DOTTED_ABBR = re.compile(r"(?:^|[\s(\[\"'])(?:(?:[A-Za-z]\.)+[A-Za-z]|[A-Z])$")


def _is_abbreviation(before: str) -> bool:
    word = re.search(r"(?:^|[\s(\[\"'])([A-Za-z.]+)$", before)
    if word and word.group(1).lower().rstrip(".") in ABBREVIATIONS:
        return True
    return bool(DOTTED_ABBR.search(before))


def split_sentences(text: str) -> list[Span]:
    """Sentence spans. Never splits on a decimal (11.2), inside parentheses, or after an abbreviation
    (E.S.R., Dr., Mrs., i.e., e.g., No., a single capital letter like 'A.'). Newlines always split."""
    cuts, depth, i, n = [], 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            depth = 0
            cuts.append(i + 1)
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch in ".!?" and depth == 0:
            j = i
            while j + 1 < n and text[j + 1] in ".!?":
                j += 1
            if (j + 1 == n or text[j + 1].isspace()) and not (ch == "." and _is_abbreviation(text[:i])):
                cuts.append(j + 1)
            i = j
        i += 1
    out, start = [], 0
    for end in cuts + [n]:
        seg = text[start:end]
        if seg.strip() and re.search(r"[A-Za-z0-9]", seg):
            lead = len(seg) - len(seg.lstrip())
            body = seg.strip()
            out.append(Span(start=start + lead, end=start + lead + len(body), text=body))
        start = end
    return out


# ------------------------------------------------------------------ claim types
Status = Literal["LOW", "HIGH", "NORMAL"]


@dataclass
class Claim:
    kind: Literal["value", "range", "status", "overstep", "group"]
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
    overstep: Optional[str] = None               # "diagnosis" | "treatment" | "reassurance" | "interpretation"
    detail: Optional[str] = None
    group: Optional[str] = None                  # group claims: "liver", "cbc", "thyroid", ... or "all"
    group_tests: set = field(default_factory=set)   # tests listed under the AI's own heading for that group


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


SUFFIX_FINDING = r"[a-z]+(?:lipidaemia|lipidemia|aemia|emia|itis|osis|pathy|megaly)"
FINDING_VERB = (r"\b(?:indicat(?:es?|ing)|suggest(?:s|ing)?|suggestive\s+of|confirm(?:s|ing)?|consistent\s+with|"
                r"points?\s+to|pointing\s+to|(?:is|are)\s+(?:a\s+)?signs?\s+of|signs?\s+of|diagnostic\s+of)\s+"
                r"(?:that\s+)?(?:you\s+(?:have|are)\s+)?(?:an?\s+|the\s+|some\s+|mild\s+|early\s+)?")
QUOTING_REPORT = re.compile(r"\b(?:report|impression|radiologist|pathologist|lab)(?:'s)?\s+(?:\w+\s+)?"
                            r"(?:says|states|mentions|notes|reads|writes|reports|lists)\b", re.I)
SYSTEM = (r"(?:thyroid|liver|kidney|kidneys|renal|hepatic|heart|cardiac|lung|lungs|pancreatic|pancreas|metabolic|"
          r"immune|bone\s+marrow|blood\s+sugar|hormonal|cardiovascular)")
INTERPRETATION = [
    re.compile(rf"\b(?:normal|healthy|good|proper|optimal|fine|excellent)\s+(?:\w+\s+)?{SYSTEM}\s+"
               r"(?:function(?:ing)?|health|status|control)\b", re.I),
    re.compile(rf"\byour\s+(?:{SYSTEM}|body|metabolism|immune\s+system)\s+(?:is|are|looks?|seems?|appears?)\s+"
               r"(?:to\s+be\s+)?(?:\w+\s+)?(?:healthy|fine|normal|in\s+good\s+(?:shape|health)|working\s+(?:well|normally|fine))\b",
               re.I),
    re.compile(r"\b(?:you(?:'re|\s+are)\s+in\s+(?:good|excellent)\s+health|(?:a\s+)?healthy\s+(?:person|individual|adult|man|woman))\b",
               re.I),
]


def _finding_words() -> str:
    gl = load_glossary()
    names = sorted({a.strip() for e in gl.values() if e.get("kind") == "finding"
                    for a in [e["term"], *e.get("aliases", [])] if len(a.strip()) > 3}, key=len, reverse=True)
    return "|".join(re.escape(n) for n in names)


_FINDING_RE: Optional[re.Pattern] = None


def _finding_claim_re() -> re.Pattern:
    global _FINDING_RE
    if _FINDING_RE is None:
        _FINDING_RE = re.compile(FINDING_VERB + rf"(?:[a-z]+[\s-]+){{0,2}}?(?:{_finding_words()}|{SUFFIX_FINDING})\b", re.I)
    return _FINDING_RE


def find_oversteps(sentence: str) -> list[tuple[str, str]]:
    """[(category, the exact phrase)] for diagnosis / treatment / reassurance / interpretation in one sentence."""
    out: list[tuple[str, str]] = []
    for why in violations(sentence):
        out.append((SAFETY_KIND.get(why, "treatment"), why))
    if not QUOTING_REPORT.search(sentence):
        m = _finding_claim_re().search(sentence)
        if m:
            out.append(("diagnosis", m.group(0).strip()))
    for rx in INTERPRETATION:
        m = rx.search(sentence)
        if m:
            out.append(("interpretation", m.group(0).strip()))
    for rx in DIAG_EXTRA:
        m = rx.search(sentence)
        if m:
            out.append(("diagnosis", m.group(0).strip()))
    for rx in TREAT_EXTRA:
        m = rx.search(sentence)
        if m:
            out.append(("treatment", m.group(0).strip()))
    for rx in REASSURE_EXTRA:
        m = rx.search(sentence)
        if m:
            out.append(("reassurance", m.group(0).strip()))
    seen, uniq = set(), []
    for cat, why in out:
        if cat not in seen:
            seen.add(cat)
            uniq.append((cat, why))
    return uniq


# ------------------------------------------------------------------ helpers
def _num(raw: str, mult: Optional[str]) -> float:
    return parse_number(raw) * MULTIPLIER.get((mult or "").lower(), 1.0)


PANEL_IDS = {"complete_blood_count", "lipid_profile", "liver_function_test", "kidney_function_test", "thyroid_profile",
             "urine_routine_examination", "differential_leukocyte_count", "electrolytes", "stool_routine_examination"}

# group name in the AI text -> (AI phrase, report section heading, glossary categories)
GROUPS = {
    "liver": (r"liver\s+(?:enzymes?|function(?:\s+tests?)|panel|tests?|profile|markers?|values?|parameters?|results?)|LFTs?|"
              r"hepatic\s+(?:panel|function\s+tests?|markers?|enzymes?)",
              r"liver|\blft\b|hepatic|bilirubin", {"Liver function (LFT)"}),
    "kidney": (r"kidney\s+(?:function(?:\s+tests?)|panel|tests?|profile|markers?|values?|results?)|renal\s+(?:function\s+tests?|"
               r"panel|profile|markers?)|KFTs?|RFTs?|electrolytes",
               r"kidney|renal|\bkft\b|\brft\b|electrolyte", {"Kidney function (KFT/RFT)", "Electrolytes"}),
    "cbc": (r"(?:complete\s+)?blood\s+counts?|blood\s+cell\s+counts?|CBC|ha?emogram|blood\s+indices",
            r"blood\s+count|\bcbc\b|ha?emogram|ha?ematology|leu[ck]ocyte\s+count|differential", {"Blood count (CBC)"}),
    "thyroid": (r"thyroid\s+(?:hormones?|profile|panel|tests?|levels?|function\s+tests?|values?|results?)|TFTs?",
                r"thyroid", {"Thyroid"}),
    "lipid": (r"lipid\s+(?:profile|panel|screen|levels?|values?|results?|tests?|numbers?)|lipids|"
              r"cholesterol\s+(?:levels|values|results|numbers)",
              r"lipid|cholesterol", {"Lipid profile"}),
    "sugar": (r"blood\s+sugar(?:\s+(?:levels?|values?|results?|tests?))?|(?:blood\s+)?glucose\s+(?:levels?|values?|results?)|"
              r"sugar\s+levels?|diabetes\s+(?:panel|tests?|markers?)",
              r"glucose|sugar|diabet", {"Diabetes / glucose"}),
    "urine": (r"urine\s+(?:tests?|results?|report|examination|routine|analysis|findings)|urinalysis",
              r"urine", {"Urine routine"}),
}
GROUP_RES = {k: re.compile(rf"\b(?:{v[0]})\b", re.I) for k, v in GROUPS.items()}
ALL_RE = re.compile(r"\b(?:all\s+(?:of\s+)?(?:your\s+|the\s+|these\s+)?(?:results|values|tests|parameters|numbers|levels|"
                    r"markers|readings)|everything|every\s+(?:result|value|test))\b", re.I)
GROUP_NORMAL_RE = re.compile(
    r"\b(?:within\s+(?:the\s+)?(?:normal\s+|standard\s+|expected\s+|healthy\s+|printed\s+)?(?:reference\s+)?"
    r"(?:ranges?|limits|intervals?)|(?:are|is|were|look|looks|appear|appears|seem|seems|came\s+back)\s+(?:all\s+|entirely\s+|"
    r"completely\s+|perfectly\s+)?(?:normal|healthy|fine|good|ok|okay|unremarkable)|all\s+(?:normal|healthy|fine|good)|"
    r"no\s+abnormalit(?:y|ies)|nothing\s+abnormal)\b", re.I)
GROUP_NEGATION = re.compile(r"\b(?:not|n't|never)\s+(?:\w+\s+){0,2}$", re.I)
HEADING_RE = re.compile(r"^\s*(?:#{1,6}\s*|\*\*)?(?P<h>[^\n*#]{2,70}?)(?:\*\*)?\s*:?\s*$")
BULLET_RE = re.compile(r"^\s*(?:[*\-•·]|\d+[.)])\s+")


def _heading_group(line: str) -> Optional[str]:
    """'**Liver Function Tests (LFT)**' -> 'liver'. A heading is a short line with no sentence punctuation."""
    if BULLET_RE.match(line) or re.search(r"[.!?]\s*$", line.strip()) or re.search(r"\d", line):
        return None
    m = HEADING_RE.match(line)
    if not m:
        return None
    h = m["h"]
    for key, rx in GROUP_RES.items():
        if rx.search(h):
            return key
    if re.search(r"\bblood\s+sugar\b|\bglucose\b|\bdiabetes\b", h, re.I):
        return "sugar"
    return "_other"                       # a heading for something else ends the previous group's context


def _group_claim(sentence: str, heading: Optional[str], mention_spans: list[tuple[int, int]] = ()) -> Optional[str]:
    nm = GROUP_NORMAL_RE.search(sentence)
    if not nm or GROUP_NEGATION.search(sentence[:nm.start()]):
        return None
    for key, rx in GROUP_RES.items():
        for gm in rx.finditer(sentence):
            # "fasting blood sugar" is one test, not the blood-sugar group
            if not any(a < gm.end() and gm.start() < b for a, b in mention_spans):
                return key
    if ALL_RE.search(sentence) or re.search(r"\b(?:they|these|those)\s+are\s+all\b|\ball\s+(?:within|normal)", sentence, re.I):
        return heading if heading and heading != "_other" else "all"
    return None


def _test_mentions(text: str) -> list[Mention]:
    gl = load_glossary()
    out = []
    for t in find_terms(text):
        if t.term_id in PANEL_IDS:
            continue                      # "CBC", "lipid profile": a group, not one test
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
            end = im.end()
            mm = re.match(r"\s*(lakhs?|lacs?|thousand|k)\b", seg[end:], re.I)       # "1.5–4.1 lakh"
            if mm:
                f = MULTIPLIER[mm.group(1).lower()]
                r = r.model_copy(update={"low": r.low * f, "high": r.high * f})
                end += mm.end()
            used.append((im.start(), end))
            claims.append(Claim(kind="range", span=_sp(text, a + im.start(), a + end), range=r, **base))
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


MD_PATTERNS = [
    re.compile(r"^[ \t]*#{1,6}[ \t]+", re.M),                   # headings
    re.compile(r"^[ \t]*(?:[*\-+•·])[ \t]+", re.M),             # bullet markers
    re.compile(r"\*\*|__"),                                   # bold
    re.compile(r"(?<![\w*])\*(?=\S)|(?<=\S)\*(?![\w*])"),        # *italic*
    re.compile(r"(?<![\w_])_(?=\S)|(?<=\S)_(?![\w_])"),           # _italic_ (never inside snake_case)
    re.compile(r"`+"),                                         # `code`
]


def strip_markdown(text: str) -> str:
    """Replace markdown markup with spaces of the same length, so every offset in the cleaned text
    is the same offset in the original (the offset map is the identity)."""
    out = text
    for rx in MD_PATTERNS:
        out = rx.sub(lambda m: " " * len(m.group(0)), out)
    assert len(out) == len(text)
    return out


def _resync(spans: list[Span], original: str) -> None:
    for sp in spans:
        sp.text = original[sp.start:sp.end]


def extract(text: str) -> list[SentenceClaims]:
    """Claims are found on markdown-free text; structure (headings, bullets, bold leads) is read from the
    original; every span's text is re-sliced from the original at the end."""
    original, text = text, strip_markdown(text)
    out = _extract(text, original)
    spans: list[Span] = []
    for sc in out:
        spans.append(sc.span)
        spans += [m.span for m in sc.mentions]
        for c in sc.claims:
            spans.append(c.span)
            if c.mention:
                spans.append(c.mention)
    _resync(spans, original)
    return out


def _extract(text: str, original: str) -> list[SentenceClaims]:
    mentions_all = _test_mentions(text)
    out: list[SentenceClaims] = []
    heading: Optional[str] = None
    heading_tests: dict[str, set] = {}
    for sp in split_sentences(text):
        sc = SentenceClaims(span=sp)
        ms = [m for m in mentions_all if sp.start <= m.span.start and m.span.end <= sp.end]
        sc.mentions = ms
        line_start = text.rfind("\n", 0, sp.start) + 1
        line_end = text.find("\n", sp.end)
        line_end = len(text) if line_end == -1 else line_end
        whole_line = original[line_start:line_end]                 # with its markdown (bullets, **bold**)
        hg = _heading_group(text[line_start:line_end]) if text[line_start:line_end].strip() == sp.text else None
        if hg:
            heading = hg
            heading_tests.setdefault(hg, set())
            out.append(sc)
            continue
        if heading and heading != "_other" and BULLET_RE.match(whole_line):
            heading_tests[heading].update(m.term_id for m in ms)
        g = _group_claim(sp.text, heading, [(m.span.start - sp.start, m.span.end - sp.start) for m in ms])
        if g:
            sc.claims.append(Claim(kind="group", span=sp, group=g, group_tests=set(heading_tests.get(g, set()))))
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
