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
    line = before[before.rfind("\n") + 1:]
    if re.fullmatch(r"\s*(?:#{1,6}\s*)?(?:[*\-]\s+)?\d{1,3}", line):
        return True                                   # "## 2. Hemoglobin", "3. Repeat the test": a list number
    word = re.search(r"(?:^|[\s(\[\"'])([A-Za-z.]+)$", before)
    if word and word.group(1).lower().rstrip(".") in ABBREVIATIONS:
        return True
    return bool(DOTTED_ABBR.search(before))


def split_sentences(text: str) -> list[Span]:
    """Sentence spans. Never splits on a decimal (11.2), inside parentheses, or after an abbreviation
    (E.S.R., Dr., Mrs., i.e., e.g., No., a single capital letter like 'A.'). Newlines always split."""
    cuts, depth, i, n = [], 0, 0, len(text)
    in_table = text.lstrip(" \t").startswith("|")
    in_quote = False
    while i < n:
        ch = text[i]
        if ch == "\n":
            depth = 0
            in_quote = False
            cuts.append(i + 1)
            in_table = text[i + 1:].lstrip(" \t").startswith("|")    # a markdown table row stays one sentence
        elif in_table:
            pass
        elif ch in '"“”':                           # never split inside a quotation: "? Early cholecystitis ..."
            in_quote = (not in_quote) if ch == '"' else (ch == "“")
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch in ".!?" and depth == 0 and not in_quote:
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
    kind: Literal["value", "range", "status", "overstep", "group", "flag", "outside_range", "quote", "certainty"]
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
    weak: bool = False                           # status carried over to a sentence that doesn't name the test
    category: Optional[str] = None               # range claims: the named category ("prediabetes", "normal")
    anchor_value: Optional[float] = None         # status claims bound by a bold value, not a test name ("**98 mg/dL**")


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
    list_id: Optional[int] = None        # consecutive bullets / numbered items / table rows share one list id
    section_id: int = 0                  # sentences between two headings share one section id


# ------------------------------------------------------------------ patterns
RANGE_CUE = re.compile(r"\b(?:range|normal|reference|between|should|ideal|healthy|limits?|interval|expected|"
                       r"typical|usual|standard|optimal|desirable|target)\b", re.I)
INTERVAL_RE = re.compile(rf"(?:between\s+)?(?P<lo>{N})%?\s*(?:-|–|—|to|and)\s*(?P<hi>{N})", re.I)
UPPER_RE = re.compile(rf"\b(?P<op>below|under|less\s+than|lower\s+than|up\s*to|not\s+more\s+than|no\s+more\s+than|"
                      rf"max(?:imum)?(?:\s+of)?)\s*(?P<n>{N})|(?P<sym><=|≤|<)\s*(?P<n2>{N})", re.I)
LOWER_RE = re.compile(rf"\b(?P<op>above|over|more\s+than|greater\s+than|higher\s+than|at\s+least|"
                      rf"min(?:imum)?(?:\s+of)?)\s*(?P<n>{N})|(?P<sym>>=|≥|>)\s*(?P<n2>{N})", re.I)
VALUE_RE = re.compile(rf"(?<![\w.,])(?P<cmp><=|>=|≤|≥|<|>)?\s*(?P<num>{N}){MULT}"
                      r"(?:\s*(?P<unit>(?:x\s*10\^?\d+\s*)?(?:[µμu]?[A-Za-z%]+(?:/[A-Za-z0-9.\^]+)*|/[A-Za-z]+)))?", re.I)
UNIT_WORDS = re.compile(r"^(?:g/dl|mg/dl|mg/l|ng/ml|pg/ml|ug/dl|µg/dl|u/l|iu/l|miu/l|µiu/ml|uiu/ml|mmol/l|meq/l|fl|pg|%|"
                        r"/cumm|/hpf|cells/cumm|million/cumm|mm/hr|mm|sec|seconds|min|ms|bpm|x10\^\d+/[a-z]+|10\^\d+/[a-z]+|"
                        r"ml/min(?:/1\.73m2)?|lakhs?|thousand|k)$", re.I)

LOW_RE = re.compile(r"\b(?:low|lower|below\s+(?:the\s+|their\s+|its\s+|your\s+)?(?:normal|reference|printed|range|limit)|decreased|"
                    r"reduced|on\s+the\s+lower\s+side)\b", re.I)    # 'deficiency' is interpretation, not a reading
HIGH_RE = re.compile(r"\b(?:high|higher|above\s+(?:the\s+|their\s+|its\s+|your\s+)?(?:normal|reference|printed|range|limit)|elevated|raised|"
                     r"increased|on\s+the\s+higher\s+side)\b", re.I)
# 'normal' is a status only when it predicates the test or its value ("X is normal", "normal at 9.0",
# "within normal", "normal X level"); attributive uses ("normal breakdown of", "normal part of") are not claims
NORMAL_WORD = r"(?:normal|fine|healthy|okay|ok|acceptable|desirable|adequate)"
NORMAL_RE = re.compile(
    r"\b(?:within\s+(?:the\s+|your\s+|their\s+)?(?:[\w-]+\s+){0,2}?(?:ranges?|limits)"
    r"|in\s+(?:the\s+)?(?:normal\s+|reference\s+|healthy\s+)?ranges?"
    r"|(?:is|are|was|were|remains?|stays?|looks?|appears?|seems?|be|being|both|all)\s+"
    r"(?:(?!higher|lower|than|above|below|not)[\w-]+\s+){0,2}?" + NORMAL_WORD +
    r"(?!\s+(?:range|limits?|upper|lower|breakdown|function|part|size|average|level\s+is))"
    r"|normal\s+(?:at|for)\b"
    r"|normal\s+(?:\w+\s+)?(?:levels?|values?|results?|readings?)(?!\s+(?:is|are)\s+(?:under|below|above|over|between)))\b",
    re.I)
# 1a: numbers that are not the AI's value for a test
THRESHOLD_BEFORE = re.compile(r"\b(?:limit|cut-?off|threshold|recommended|recommends|ideally|ideal|target|goal|"
                              r"should\s+be|keep(?:ing)?\s+(?:it|this|them)?\s*(?:under|below|above)?)\s+"
                              r"(?:of\s+|is\s+|at\s+|to\s+)?(?:the\s+)?$", re.I)
NOT_A_VALUE_AFTER = re.compile(r"^(?:\s*(?:mg/dl|%|u/l)\s+)?\s*(?:limit|cut-?off|threshold|mark)\b"
                               r"|^(?:\s*[-–]\s*\d+(?:\.\d+)?)?[\s-]*(?:months?|weeks?|days?|hours?|hrs?|years?|yrs?|minutes?|mins?)\b"
                               r"|^\s+(?:different\s+)?(?:values?|markers?|tests?|readings?|results?|numbers?|times?)\b", re.I)
OR_BELOW_TXT = re.compile(rf"(?P<n>{N})%?\s*(?:or\s+less|or\s+lower|or\s+below|or\s+under|and\s+below|and\s+under)\b", re.I)
AND_ABOVE_TXT = re.compile(rf"(?P<n>{N})%?\s*(?:or\s+higher|or\s+more|or\s+above|and\s+above|and\s+higher)\b", re.I)
# "using standard clinical reference ranges", "standard ranges usually prefer ...": ranges imported from elsewhere
OUTSIDE_RANGES = re.compile(
    r"\b(?:using|use|uses|based\s+on|according\s+to|applying|with)\s+(?:the\s+)?(?:standard|typical|general|common|"
    r"usual|textbook|population|clinical)\s+(?:clinical\s+|lab(?:oratory)?\s+)?(?:reference\s+)?(?:ranges?|intervals?|"
    r"cut-?offs?|guidelines?)\b"
    r"|\bstandard\s+(?:clinical\s+|lab\s+)?(?:reference\s+)?ranges?\s+(?:usually|typically|generally|say|prefer|are|put)\b",
    re.I)
# a named category with a threshold, e.g. "Borderline high is 200 to 239"
CATEGORY_RE = re.compile(
    r"^\W*(?P<cat>normal|healthy|optimal|near[\s-]optimal|desirable|borderline(?:[\s-]high|[\s-]low)?|high|very\s+high|"
    r"low|pre-?diabetes|pre-?diabetic|diabetes|diabetic|impaired\s+fasting\s+glucose|insufficien(?:t|cy)|"
    r"deficien(?:t|cy)|sufficient|stage\s+\w+)\s+(?:is|are|range\s+is|=|:|\()", re.I)
NORMAL_CATEGORIES = {"normal", "healthy", "optimal", "desirable", "adequate", "sufficient", "ideal"}
BOLD_VALUE = re.compile(rf"\*\*\s*(?P<num>{N})\s*(?P<unit>[A-Za-zµμ%/]+(?:/[A-Za-z]+)?)?\s*\*\*")
COMPARISON_BEFORE = re.compile(r"\b(?:similar\s+to|like|unlike|as\s+with|as\s+for|compared\s+(?:to|with)|same\s+as)\s+$",
                               re.I)
SUGGEST_TEST = re.compile(r"\b(?:look\s+at|order|recommend|suggest|consider|check|repeat|get|do)\s+(?:an?\s+|the\s+|"
                          r"another\s+)?(?:\w+\s+){0,2}?tests?\b", re.I)
NEGATION = re.compile(r"(?:\b(?:not|isn't|aren't|wasn't|hasn't|haven't|hadn't|never|no|without)|n't)\s+(?:\w+\s+){0,2}$", re.I)
HYPOTHETICAL = re.compile(r"\b(?:if|would|could|might|unless|whether|were)\b[^,.;:]*$", re.I)
DESCRIBES_CELLS = re.compile(r"^\s*(?:\w+\s+){0,2}?(?:size|sizes|shape|shapes|shaped|variation|cells?|ability|function|"
                             r"health|capacity)\b", re.I)
FLAG_NEAR = re.compile(r"\bflag", re.I)
FLAG_MISMATCH_RE = re.compile(r"\bflag(?:ged)?\b.*\b(?:but|however|although|though|yet)\b.*\b(?:within|in)\s+(?:the\s+)?"
                              r"(?:\w+\s+){0,3}(?:range|limits?|interval)\b", re.I | re.S)

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
    re.compile(r"\byou(?:'re|\s+are)\s+(?:in\s+(?:good|excellent)\s+health|a\s+healthy\s+(?:person|individual|adult|man|woman))\b",
               re.I),                    # 'a healthy adult typically has 90+' describes the reference population
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


CONDITION_WORDS = (r"an(?:a)?emia|infections?|inflammation|inflammatory|immune\s+response|deficienc(?:y|ies)|illness(?:es)?|"
                   r"diseases?|disorders?|stress|dehydration|insufficiency|failure|damage")
LINK = (r"\b(?:points?\s+to|pointing\s+to|indicat(?:es?|ing)(?:\s+that)?|suggest(?:s|ing)?(?:\s+that)?|suggestive\s+of|"
        r"(?:is|are)\s+(?:often\s+|usually\s+|commonly\s+|typically\s+)?(?:an?\s+)?(?:early\s+)?signs?\s+of|"
        r"(?:an?\s+)?(?:early\s+)?signs?\s+of|usually\s+means|means\s+that|consistent\s+with|typical\s+of|"
        r"(?:commonly\s+)?occurs?\s+(?:together\s+)?(?:during|with|in)|commonly\s+occur|due\s+to|caused\s+by|"
        r"(?:a|the)\s+result\s+of|reacting\s+to|recovery\s+from|shows?\s+(?:\w+\s+){0,3}patterns?|aligns?\s+with)\b")
DEFINITION = re.compile(r"\b(?:is\s+(?:a|an)\s+(?:\w+\s+){0,2}(?:marker|measure|test)\s+(?:for|of)|measures|is\s+used\s+to|"
                        r"used\s+to\s+(?:check|detect|screen|look))\b", re.I)
DEFERRAL = re.compile(r"\b(?:a|your|the)\s+(?:doctor|physician|clinician|healthcare\s+provider)\s+(?:would|will|can|may|might|"
                      r"should|could)\s+(?:\w+\s+){0,3}(?:review|determine|decide|assess|check|evaluate|interpret|advise|"
                      r"confirm|look)\b", re.I)
HEALTH_ABILITY = re.compile(r"\b(?:normal|healthy|good|proper|optimal|excellent)\s+(?:\w+\s+){0,2}(?:function(?:ing)?|"
                            r"ability|capacity|health)\b(?!\s+tests?)", re.I)
_LINK_RE: Optional[re.Pattern] = None


def _link_re() -> re.Pattern:
    global _LINK_RE
    if _LINK_RE is None:
        cond = rf"(?:{_finding_words()}|{SUFFIX_FINDING.replace('megaly)', 'megaly|cytosis|penia)')}|{CONDITION_WORDS})"
        _LINK_RE = re.compile(LINK + rf"[^.;!?]{{0,80}}?\b{cond}\b", re.I)
    return _LINK_RE


def _lexicon_res() -> dict:
    global _LEX
    if _LEX is None:
        from app.verify import lexicon as L
        cond = [rf"[a-z]+(?:{'|'.join(L.CONDITION_SUFFIXES)})", *L.CONDITIONS]
        fw = _finding_words()
        if fw:
            cond.append(fw)
        _LEX = {
            "condition": re.compile(rf"\b(?:{'|'.join(cond)})\b", re.I),
            "not_word": re.compile(rf"^(?:{'|'.join(L.NOT_CONDITION_WORDS)})$", re.I),
            "not_after": re.compile(L.NOT_CONDITION_AFTER, re.I),
            "not_before": re.compile(L.NOT_CONDITION_BEFORE, re.I),
            "state": re.compile(rf"\b(?:{'|'.join(L.ORGAN_STATES + L.ORGAN_FUNCTION + L.HEALTH_JUDGEMENTS + L.CAUSES)})",
                                re.I),
            "reassurance": re.compile(rf"\b(?:{'|'.join(L.REASSURANCE)})\b", re.I),
            "lifestyle": re.compile(rf"^\W*(?:{'|'.join(L.LIFESTYLE_VERBS)})\b(?=.*\b(?:{'|'.join(L.LIFESTYLE_OBJECTS)})\b)",
                                    re.I),
            "advice": re.compile(rf"\b(?:{'|'.join(L.ADVICE)})\b", re.I),
            "definition": re.compile("|".join(L.DEFINITIONS), re.I),
            "def_after_test": re.compile(L.DEFINITION_AFTER_TEST, re.I),
            "deferral": re.compile("|".join(L.DEFERRALS), re.I),
            "attribution": re.compile(r"\b(?:" + "|".join(L.ATTRIBUTION) + r")\b", re.I),
            "ai_hedge": re.compile(r"\b(?:" + "|".join(h for h in L.AI_HEDGES if h != r"\?") + r")\b", re.I),
        }
    return _LEX


_LEX: Optional[dict] = None


def _default_interpretation(sentence: str, mention_spans: list[tuple[int, int]]) -> Optional[str]:
    """The default rule: a CONDITION/STATE term from the lexicon, in a sentence that is not EXEMPT
    (a definition, a doctor deferral, quoting the report, or defining a category threshold)."""
    lx = _lexicon_res()
    if lx["deferral"].search(sentence) or QUOTING_REPORT.search(sentence):
        return None
    if lx["attribution"].search(sentence) and lx["ai_hedge"].search(sentence):
        return None                               # "a mention of possible early ...": faithful to the report's hedge
    if lx["definition"].search(sentence) or any(lx["def_after_test"].match(sentence[e:]) for _, e in mention_spans):
        return None
    if CATEGORY_RE.match(sentence) and re.search(r"\d", sentence):
        return None                               # "Borderline high is 200 to 239": a range statement
    inside = lambda a, b: any(s <= a and b <= e for s, e in mention_spans)  # noqa: E731
    for m in lx["condition"].finditer(sentence):
        word = m.group(0)
        if lx["not_word"].match(word) or inside(m.start(), m.end()):
            continue
        if lx["not_after"].match(sentence[m.end():]) or lx["not_before"].search(sentence[:m.start()]):
            continue
        return word
    m = lx["state"].search(sentence) or lx["reassurance"].search(sentence) or lx["lifestyle"].search(sentence)
    if m:
        return m.group(0)
    m = lx["advice"].search(sentence)
    if m:
        return m.group(0)
    return None


def find_oversteps(sentence: str, mention_spans: list[tuple[int, int]] = ()) -> list[tuple[str, str]]:
    """[(category, the exact phrase)] for diagnosis / treatment / reassurance / interpretation in one sentence."""
    out: list[tuple[str, str]] = []
    deferred = bool(DEFERRAL.search(sentence))      # "a doctor would review/determine ...": deferring, not advising
    for why in violations(sentence):
        cat = SAFETY_KIND.get(why, "treatment")
        if not (deferred and cat == "treatment"):
            out.append((cat, why))
    if not QUOTING_REPORT.search(sentence) and not deferred:
        m = _finding_claim_re().search(sentence)
        if m:
            out.append(("link", m.group(0).strip()))
        else:
            for lm in _link_re().finditer(sentence):
                if DEFINITION.search(sentence[:lm.start()]) and not re.search(r"\b(?:because|which|this|these|so)\b",
                                                                             sentence[:lm.start()], re.I):
                    continue                        # e.g. 'CRP is a marker of inflammation': a definition
                out.append(("link", lm.group(0).strip()))
                break
    if not deferred:
        for rx in [*INTERPRETATION, HEALTH_ABILITY]:
            m = rx.search(sentence)
            if m:
                out.append(("interpretation", m.group(0).strip()))
                break
    for rx in DIAG_EXTRA:
        m = rx.search(sentence)
        if m:
            out.append(("diagnosis", m.group(0).strip()))
    for rx in TREAT_EXTRA:
        m = rx.search(sentence)
        if m and not deferred:
            out.append(("treatment", m.group(0).strip()))
    for rx in REASSURE_EXTRA:
        m = rx.search(sentence)
        if m:
            out.append(("reassurance", m.group(0).strip()))
    if not out:                                   # the default rule (lexicon), when nothing more specific fired
        phrase = _default_interpretation(sentence, list(mention_spans))
        if phrase:
            out.append(("condition", phrase))
    seen, uniq = set(), []
    for cat, why in out:
        key = "diagnosis" if cat == "link" else cat       # a condition named via 'sign of' / 'points to': count once
        if key not in seen:
            seen.add(key)
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
    "thyroid_hormones": (r"thyroid\s+hormones?(?:\s+levels?)?", r"(?!)", set()),
    "thyroid": (r"thyroid\s+(?:profile|panel|tests?|levels?|function\s+tests?|values?|results?)|TFTs?",
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
GROUPS["dlc"] = (r"differentials?|differential\s+(?:leu[ck]ocyte\s+)?counts?|DLC", r"differential|\bdlc\b", set())
# groups defined by their member tests rather than by section or category
GROUP_TERMS = {"thyroid_hormones": {"triiodothyronine", "thyroxine", "free_t3", "free_t4"},   # never TSH
               "dlc": {"neutrophils", "lymphocytes", "eosinophils", "monocytes", "basophils", "absolute_neutrophil_count",
                       "absolute_lymphocyte_count", "band_forms"}}
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
    if BULLET_RE.match(line) or re.search(r"[.!?][\"”']?\s*$", line.strip()) or re.search(r"\d", line) \
            or line.lstrip().startswith(('"', "“")):
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


def _cutoff_re() -> re.Pattern:
    from app.verify import lexicon as L
    rng = (rf"(?:(?:below|under|less\s+than|above|over|more\s+than|<=|>=|≤|≥|<|>)\s*{N}|{N}\s*(?:[-–]|to)\s*{N}|"
           rf"{N}\s*(?:or\s+(?:above|more|higher|below|less|lower)|\+|and\s+(?:above|over)))")
    return re.compile(rf"^\W*(?P<range>{rng})\s*%?\s*[a-zA-Z/]*\s*[:=–]\s*(?P<cat>{L.CUTOFF_CATEGORIES})\b", re.I)


CUTOFF_RE = _cutoff_re()
HEAD_NOUNS = {"ratio", "interval", "rate", "duration", "index", "count", "score"}


def _head_noun_ref(sentence: str, term: str) -> bool:
    """The sentence refers to the topic test by its head noun alone: "A standard ratio ..." after
    "cardiothoracic ratio" (only for measurement nouns, never for a word like "level")."""
    head = term.split()[-1].lower() if term.split() else ""
    return head in HEAD_NOUNS and bool(re.search(
        rf"\b(?:a|the|this|your|its|that|standard|normal|typical|usual|healthy)\s+(?:\w+\s+)?{head}\b", sentence, re.I))


TOPIC_REF = re.compile(r"\b(?:your|this|the)\s+(?:level|result|value|reading|number)s?\b", re.I)


# a list of possible causes of the results: "Common contributing factors include:", "It can be caused by:"
CAUSE_LIST_INTRO = re.compile(r"\b(?:factors?|causes?|reasons?)\s+(?:include|includes|can\s+include|are|may\s+be)\b|"
                              r"\bcan\s+be\s+caused\s+by\b|\bpossible\s+causes?\b|\bcontributing\s+factors?\b", re.I)
POSSIBILITY_INTRO = re.compile(r"\b(?:include|includes|including|such\s+as|causes?|factors?|symptoms?|reasons?|"
                               r"conditions?|possibilities|experience|could\s+be|might\s+be|can\s+be)\b[^:]*:\s*$", re.I)


QUANTIFIER = re.compile(r"\b(?:most|many|some|a\s+few|several|(?:the\s+)?(?:vast\s+)?majority|nearly\s+all|almost\s+all)\b", re.I)


def _group_claim(sentence: str, heading: Optional[str], mention_spans: list[tuple[int, int]] = ()) -> Optional[str]:
    nm = GROUP_NORMAL_RE.search(sentence)
    if not nm or GROUP_NEGATION.search(sentence[:nm.start()]):
        return None
    if QUANTIFIER.search(sentence[:nm.start()]):
        return None                               # "Most of your liver tests are fine" is not an 'all' claim
    for key, rx in GROUP_RES.items():
        for gm in rx.finditer(sentence):
            # "fasting blood sugar" is one test, not the blood-sugar group
            if not any(a < gm.end() and gm.start() < b for a, b in mention_spans):
                return key
    if ALL_RE.search(sentence) or re.search(r"\b(?:they|these|those)\s+are\s+all\b|\ball\s+(?:within|normal)", sentence, re.I):
        return heading if heading and heading != "_other" else "all"
    return None


# descriptive phrases that the glossary maps to a count, but that are never the subject of a status
GENERIC_MENTIONS = {"red blood cells", "red blood cell", "white blood cells", "white blood cell", "blood cells",
                    "leukocytes", "leucocytes", "erythrocytes", "rbcs", "wbcs", "platelet", "cells"}
LEAD_RE = re.compile(r"^\s*(?:[*\-+•·]|\d+[.)])\s+\*\*(?P<lead>[^*]+?)\*\*")
LEAD_STATUS_RE = re.compile(r"(low|high|normal|elevated|raised|decreased|reduced|borderline\s+high|borderline\s+low)\b", re.I)
LEAD_STATUS = {"low": "LOW", "decreased": "LOW", "reduced": "LOW", "high": "HIGH", "elevated": "HIGH", "raised": "HIGH",
               "normal": "NORMAL", "borderline": "HIGH"}


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


def _find_statuses(text: str, a: int, b: int, used: list[tuple[int, int]] = ()) -> list[tuple[str, int, int]]:
    """Status words in text[a:b] that really are claims about a value: (status, start, end), absolute offsets,
    at most one per status kind."""
    seg = text[a:b]
    out = []
    for rx, st in ((LOW_RE, "LOW"), (HIGH_RE, "HIGH"), (NORMAL_RE, "NORMAL")):
        for sm in rx.finditer(seg):
            if any(not (sm.end() <= us or sm.start() >= ue) for us, ue in used):
                continue
            if not _is_status_claim(text, a + sm.start(), a + sm.end(), seg[:sm.start()]):
                continue
            out.append((st, a + sm.start(), a + sm.end()))
            break
    return out


def _is_status_claim(text: str, s: int, e: int, before: str) -> bool:
    """False for status words that are negated, hypothetical, describe the lab's flag, or describe cells/variation."""
    if NEGATION.search(before) or HYPOTHETICAL.search(before):
        return False
    line_a = text.rfind("\n", 0, s) + 1
    left = text[line_a:s]
    # inside "quotes" or (brackets) next to the word 'flag', e.g. a lab's "L" (Low) marker
    in_quotes = left.count('"') % 2 == 1
    in_paren = left.rfind("(") > left.rfind(")")
    if in_quotes or in_paren:
        close = text.find(")" if in_paren else '"', e)
        around = text[max(line_a, s - 30):(close if close != -1 else e) + 30]
        if FLAG_NEAR.search(around):
            return False
    # describing cells or abilities, e.g. 'some normal, some large', 'normal average size', 'normal pumping ability'
    if re.search(r"\bsome\s+$", left, re.I) or DESCRIBES_CELLS.match(text[e:e + 40]):
        return False
    return True


# ---------------------------------------------------------------- markdown tables
TABLE_ROLES = [
    ("reference", re.compile(r"\b(?:reference|range|normal|ref|interval|limits?)\b", re.I)),
    ("result", re.compile(r"\b(?:result|results|value|values|level|reading)\b", re.I)),
    ("test", re.compile(r"\b(?:test|tests|parameter|investigation|marker|analyte|name|organ)\b", re.I)),
    ("interp", re.compile(r"\b(?:interpretation|finding|findings|assessment|status|flag|meaning|comment)\b", re.I)),
]
SEPARATOR_ROW = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
CELL_LOW = re.compile(r"\b(?:very\s+low|low|lower|below|decreased|reduced|suppressed|deficient)\b", re.I)
CELL_HIGH = re.compile(r"\b(?:very\s+high|high|higher|elevated|raised|increased|above)\b", re.I)
CELL_NORMAL = re.compile(r"^\W*(?:normal|desirable|acceptable|adequate|optimal|within(?:\s+range)?|negative|non-?reactive)\b",
                         re.I)
CELL_VALUE = re.compile(rf"^(?P<cmp><=|>=|≤|≥|<|>)?\s*(?P<num>{N})(?:\s*[-–]\s*(?P<hi>{N}))?\s*(?P<unit>[^\s].*)?$")


def _cells(text: str, a: int, b: int) -> list[tuple[int, int]]:
    """Content spans of the cells of a '| a | b |' row (absolute, stripped)."""
    line = text[a:b]
    bars = [i for i, ch in enumerate(line) if ch == "|"]
    out = []
    for x, y in zip(bars, bars[1:]):
        seg = line[x + 1:y]
        lead = len(seg) - len(seg.lstrip())
        body = seg.strip()
        out.append((a + x + 1 + lead, a + x + 1 + lead + len(body)))
    return out


def parse_tables(text: str) -> dict[int, dict]:
    """line start -> {"kind": "header"|"separator"|"row", "roles": {role: [cell spans]}} for markdown tables."""
    out: dict[int, dict] = {}
    pos, roles = 0, None
    for line in text.split("\n"):
        a, b = pos, pos + len(line)
        pos = b + 1
        if not line.lstrip(" \t").startswith("|"):
            roles = None
            continue
        if SEPARATOR_ROW.match(line):
            out[a] = {"kind": "separator"}
            continue
        cells = _cells(text, a, b)
        if roles is None:                         # the first row of a table is its header
            roles = []
            for cs, ce in cells:
                h = text[cs:ce]
                roles.append(next((r for r, rx in TABLE_ROLES if rx.search(h)), "other"))
            if "test" not in roles and roles:
                roles[0] = "test"                     # first column names the row
            out[a] = {"kind": "header"}
            continue
        mapped: dict[str, list] = {}
        for i, span in enumerate(cells):
            mapped.setdefault(roles[i] if i < len(roles) else "other", []).append(span)
        out[a] = {"kind": "row", "roles": mapped}
    return out


def _cell_status(cell: str) -> Optional[str]:
    if re.search(r"\b(?:not|no|n't)\b", cell, re.I):
        return None
    lo, hi = CELL_LOW.search(cell), CELL_HIGH.search(cell)
    if lo or hi:
        return "LOW" if lo and (not hi or lo.start() < hi.start()) else "HIGH"
    return "NORMAL" if CELL_NORMAL.match(cell) else None


def _table_row_claims(text: str, row: dict) -> tuple[list[Mention], list[Claim]]:
    """One row = one claim set, from explicit cells: value, printed range and status."""
    gl = load_glossary()
    roles = row["roles"]
    test_cells = roles.get("test", [])
    if not test_cells:
        return [], []
    ts, te = test_cells[0]
    hits = [t for t in find_terms(text[ts:te]) if t.term_id not in PANEL_IDS and
            (gl.get(t.term_id, {}).get("kind", "test") == "test" or gl.get(t.term_id, {}).get("possibilities"))]
    if not hits:
        return [], []
    t = hits[0]
    m = Mention(term_id=t.term_id, term=t.term, span=_sp(text, ts + t.span.start, ts + t.span.end))
    base = dict(term_id=m.term_id, term=m.term, mention=m.span)
    claims: list[Claim] = []
    value_cells = roles.get("result", []) + [c for c in roles.get("interp", []) if CELL_VALUE.match(text[c[0]:c[1]])]
    for cs, ce in value_cells:
        vm = CELL_VALUE.match(text[cs:ce])
        if not vm:
            continue
        unit = (vm["unit"] or "").strip() or None
        if unit and not UNIT_WORDS.match(unit.replace(" ", "")):
            unit = None
        claims.append(Claim(kind="value", span=_sp(text, cs, ce), value=parse_number(vm["num"]),
                            value_high=parse_number(vm["hi"]) if vm["hi"] else None, value_raw=text[cs:ce],
                            unit=unit, **base))
        break
    for cs, ce in roles.get("reference", []):
        r = parse_range(text[cs:ce])
        if r.kind != "unparseable":
            claims.append(Claim(kind="range", span=_sp(text, cs, ce), range=r, **base))
    for cs, ce in roles.get("interp", []):
        st = _cell_status(text[cs:ce])
        if st and not CELL_VALUE.match(text[cs:ce]):
            claims.append(Claim(kind="status", span=_sp(text, cs, ce), status=st, **base))
            break
    return [m], claims


# ---------------------------------------------------------------- quotes and certainty
QUOTE_RE = re.compile(r'["“]\s*([^"“”]{12,}?)\s*["”]')
QUOTE_INTRO = re.compile(r"\b(?:report|radiologist|doctor|lab|pathologist|impression|scan|ultrasound)\b[^.!?]*\b(?:says|states|stated|"
                         r"has\s+written|wrote|writes|reads|notes|mentions|lists)\s*:\s*$", re.I)
IMPRESSION_LINE = re.compile(r"^\W*(?:impression|conclusion|opinion)\s*:\s*\S", re.I)
NEGATED_CERTAINTY = re.compile(r"\b(?:does\s+not|doesn't|do\s+not|don't|cannot|can't|not\s+enough\s+to|unable\s+to|"
                               r"did\s+not)\s+(?:\w+\s+){0,2}?(?:confirm|establish|tell|rule\s+out|prove|show|determine|"
                               r"diagnose|mean)\b", re.I)


def _lex_alt(items) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(items) + r")\b", re.I)


def never_claim_type(sentence: str, line: str) -> Optional[str]:
    """Sentence types that are never claims, checked before anything is reported:
    'question', 'deferral', 'safety' (no claims at all); 'negated' (the negated clause is not a claim);
    'heading' (status words still checked, never OVERSTEPS); 'definition' (no default-lexicon OVERSTEPS)."""
    from app.verify import lexicon as L
    st = sentence.strip()
    if st.endswith("?") or re.match(L.QUESTION_START, st, re.I):
        return "question"
    if re.match(r"^\s*#{1,6}\s", line) or re.match(r"^\s*\*\*[^*]+\*\*\s*:?\s*$", line):
        return "heading"
    if not re.search(r"[.!?:]\s*$", st) and len(st.split()) <= 6 and not BULLET_RE.match(line) and not re.search(r"\d", st):
        return "heading"                            # a line that is only a label ("Subclinical hypothyroidism")
    if any(re.search(p, st, re.I) for p in L.SAFETY_ADVICE):
        return "safety"
    if _lexicon_res()["deferral"].search(st):
        return "deferral"
    if NEGATED_CERTAINTY.search(st):
        return "negated"
    dm = re.match(L.DEFINITION_SENTENCE, st, re.I)
    if dm and not re.search(L.PATIENT_RESULT, st, re.I):
        # the subject must be a medical term ("Hepatomegaly means ..."), not "The main finding is a ..."
        gl = load_glossary()
        subject = st[:dm.end()]
        if any(gl.get(t.term_id, {}).get("kind") in ("test", "finding", "abbreviation") for t in find_terms(subject)):
            return "definition"
    return None


def _apply_never_claim(sc: "SentenceClaims", kind: Optional[str], stext: str, mention_spans) -> None:
    if kind is None:
        return
    if kind in ("question", "deferral", "safety"):
        sc.claims = []
        return
    if kind == "heading":
        sc.claims = [c for c in sc.claims if c.kind in ("value", "range", "status")]
        return
    if kind == "negated":
        # "X does not confirm Y": drop that clause; whatever else the sentence says is still checked
        rest = NEGATED_CERTAINTY_CLAUSE.sub(" ", stext)
        keep = {cat for cat, _ in find_oversteps(rest, [])}
        sc.claims = [c for c in sc.claims if c.kind not in ("overstep", "certainty") or
                     (c.kind == "overstep" and c.overstep in keep)]
        return
    if kind == "definition":
        sc.claims = [c for c in sc.claims if not (c.kind == "certainty" or (c.kind == "overstep" and c.overstep == "condition"))]


NEGATED_CERTAINTY_CLAUSE = re.compile(r"(?:,?\s*(?:but|however|although|though)\s+)?[^.;,]*\b(?:does\s+not|doesn't|do\s+not|don't|cannot|"
                                      r"can't|not\s+enough\s+to|unable\s+to|did\s+not)\s+[^.;]*", re.I)


def _certainty_candidate(sentence: str, gl: dict) -> list[str]:
    """Finding terms the AI states about this patient without any hedge (checked against the report's hedges)."""
    from app.verify import lexicon as L
    if _lex_alt(L.AI_HEDGES).search(sentence) or NEGATED_CERTAINTY.search(sentence):
        return []
    if not _lex_alt(L.PATIENT_REFERENCE).search(sentence) or _lexicon_res()["definition"].search(sentence):
        return []
    return sorted({t.term_id for t in find_terms(sentence) if gl.get(t.term_id, {}).get("kind") == "finding"})


# a censored value right after the test name is the AI's value, not a range: "TSH <0.01: ..."
LEAD_CENSORED = re.compile(rf"^\s*(?:[:=]|is|of|at|was|=)?\s*(?P<cmp><=|>=|≤|≥|<|>)\s*(?P<num>{N})(?![\d.])")


DOSE_AFTER = re.compile(r"\s*(?:mg|mcg|µg|iu|tablets?|tabs?|capsules?|drops?)(?![/\w])", re.I)


COMPOUND_ITEM = re.compile(rf"(?P<sub>[A-Za-z][A-Za-z ]{{0,20}}?)\s*[:=]\s*(?P<num>{N})")


def _compound(text: str, m: Mention) -> tuple[list[Mention], list[Claim]]:
    """'Bilirubin (Total: 1.6, Direct: 0.4, Indirect: 1.2 mg/dL)' -> mentions + value claims for
    Total, Direct and Indirect Bilirubin. Needs >= 2 'label: number' items naming known tests."""
    # the bracket may start inside the matched alias ("Bilirubin (Total" matches the alias 'Bilirubin (Total)')
    open_at = text.find("(", m.span.start, m.span.end)
    if open_at == -1:
        pm0 = re.match(r"\s*\(", text[m.span.end:])
        if not pm0:
            return [], []
        open_at = m.span.end + pm0.end() - 1
    close_at = text.find(")", open_at)
    if close_at == -1 or close_at - open_at > 120:
        return [], []
    base = text[m.span.start:open_at].strip() if open_at < m.span.end else m.span.text
    inner_start, inner = open_at + 1, text[open_at + 1:close_at]
    unit_m = re.search(r"(?P<unit>[A-Za-zµμ%][A-Za-z/%µμ0-9.]*)\s*$", inner)
    unit = unit_m["unit"] if unit_m and UNIT_WORDS.match(unit_m["unit"]) else None
    gl = load_glossary()
    mentions, claims = [], []
    for it in COMPOUND_ITEM.finditer(inner):
        sub = it["sub"].strip()
        tid = None
        for cand in (f"{sub} {base}", f"{base} {sub}", f"{sub} {m.term}"):
            hit = [t for t in find_terms(cand) if gl.get(t.term_id, {}).get("kind", "test") == "test"
                   and t.span.end - t.span.start == len(cand)]
            if hit:
                tid = hit[0].term_id
                break
        if not tid:
            continue
        ss = inner_start + it.start("sub") + (len(it["sub"]) - len(it["sub"].lstrip()))
        mention = Mention(term_id=tid, term=gl[tid]["term"], span=_sp(text, ss, ss + len(sub)))
        ns, ne = inner_start + it.start("num"), inner_start + it.end("num")
        mentions.append(mention)
        claims.append(Claim(kind="value", span=_sp(text, ns, ne), value=parse_number(it["num"]),
                            value_raw=it["num"], unit=unit, term_id=tid, term=mention.term, mention=mention.span))
    if len(mentions) < 2:
        return [], []
    return mentions, claims


PRE_ADJ = re.compile(r"(?P<adj>(?:(?:very|mildly|slightly|significantly|markedly|severely|moderately)\s+)?"
                     r"(?:low|high|elevated|raised|decreased|reduced|increased))\s+$", re.I)


def _segment_claims(text: str, m: Mention, a: int, b: int, sent_start: int) -> list[Claim]:
    """Claims in text[a:b], the part of the sentence that belongs to mention m."""
    seg = text[a:b]
    claims: list[Claim] = []
    used: list[tuple[int, int]] = []

    def free(s: int, e: int) -> bool:
        return all(e <= us or s >= ue for us, ue in used)

    base = dict(term_id=m.term_id, term=m.term, mention=m.span)
    lc = LEAD_CENSORED.match(seg)
    if lc and not RANGE_CUE.search(text[sent_start:a]):
        vs, ve = lc.start("cmp"), lc.end("num")
        claims.append(Claim(kind="value", span=_sp(text, a + vs, a + ve), value=parse_number(lc["num"]),
                            value_raw=seg[vs:ve], **base))
        used.append((vs, ve))
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
    for am in AND_ABOVE_TXT.finditer(seg):           # "a score of 90 or higher"
        if free(am.start(), am.end()):
            r = ReferenceRange(raw=am.group(0), kind="lower", low=parse_number(am["n"]), low_inclusive=True)
            used.append((am.start(), am.end()))
            claims.append(Claim(kind="range", span=_sp(text, a + am.start(), a + am.end()), range=r, **base))
    for om in OR_BELOW_TXT.finditer(seg):            # "generally 0.50 or less"
        if free(om.start(), om.end()):
            r = ReferenceRange(raw=om.group(0), kind="upper", high=parse_number(om["n"]), high_inclusive=True)
            used.append((om.start(), om.end()))
            claims.append(Claim(kind="range", span=_sp(text, a + om.start(), a + om.end()), range=r, **base))
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
        if THRESHOLD_BEFORE.search(seg[:vm.start("num")]) or NOT_A_VALUE_AFTER.search(seg[vm.end("num"):]):
            continue                              # "the recommended 200 limit", "3 months", "two values"
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
    for st, sa, sb in _find_statuses(text, a, b, used):
        claims.append(Claim(kind="status", span=_sp(text, sa, sb), status=st, **base))
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
    re.compile(r"\\(?=[^\w\s])"),                                # escapes: "\<0.01", "1\."
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
    bullet_line, lead_mentions, lead_done, lead_word = -1, [], False, None
    prev_lead: list[Mention] = []            # the last bullet lead, for follow-on bullets that name no test
    quote_mode = False                       # after "Your report says:", the following bullets are quotes
    topic: Optional[Mention] = None          # the test a heading or bullet is about
    list_counter, last_item_line = 0, -1
    section_counter, last_heading_line = 0, -1
    possibility_list = False                 # inside a list introduced by "... include:" / "You might experience:"
    cause_list = False                       # ... and that list gives possible causes of the results
    tables = parse_tables(text)
    for sp in split_sentences(text):
        sc = SentenceClaims(span=sp)
        ms = [m for m in mentions_all if sp.start <= m.span.start and m.span.end <= sp.end]
        sc.mentions = ms
        line_start = text.rfind("\n", 0, sp.start) + 1
        line_end = text.find("\n", sp.end)
        line_end = len(text) if line_end == -1 else line_end
        whole_line = original[line_start:line_end]                 # with its markdown (bullets, **bold**)
        # items under "Common causes include:" / "You might experience:" are possibilities, not test claims
        in_possibility_list = possibility_list and bool(BULLET_RE.match(whole_line))
        in_cause_list = cause_list and bool(BULLET_RE.match(whole_line))
        if POSSIBILITY_INTRO.search(text[sp.start:sp.end]):
            possibility_list = True
            cause_list = bool(CAUSE_LIST_INTRO.search(text[sp.start:sp.end]))
            if cause_list:                         # the lead-in itself: "Common contributing factors include:"
                sc.claims.append(Claim(kind="overstep", span=sp, overstep="cause_list",
                                       detail=text[sp.start:sp.end].strip().rstrip(":")))
        elif not BULLET_RE.match(whole_line):
            possibility_list = cause_list = False
        if re.match(r"^\s*(?:#{1,6}\s|\*\*[^*]+\*\*\s*:?\s*$|-{3,}\s*$)", whole_line) and line_start != last_heading_line:
            section_counter += 1                       # a markdown heading, a bold-only line or a rule
            last_heading_line = line_start
        sc.section_id = section_counter
        if BULLET_RE.match(whole_line) or whole_line.lstrip().startswith("|"):
            if line_start != last_item_line:
                prev_text = original[:line_start].rstrip("\n")
                prev_line = prev_text[prev_text.rfind("\n") + 1:]
                if not (BULLET_RE.match(prev_line) or prev_line.lstrip().startswith("|")):
                    list_counter += 1                  # a new list starts here
                last_item_line = line_start
            sc.list_id = list_counter
        trow = tables.get(line_start)
        if trow:                                   # markdown table: header/separator rows are not claims
            if trow["kind"] == "row":
                sc.mentions, sc.claims = _table_row_claims(text, trow)
                # the name column is a label ("| Ascites | Absent |"); interpretation is judged in the other cells
                name_cells = trow["roles"].get("test", [])[:1]
                rest = text[sp.start:sp.end]
                for a, b in name_cells:
                    rest = rest[:a - sp.start] + " " * (b - a) + rest[b - sp.start:]
                for cat, why in find_oversteps(rest, []):
                    sc.claims.append(Claim(kind="overstep", span=sp, overstep=cat, detail=why))
            out.append(sc)
            continue
        hg = _heading_group(text[line_start:line_end]) if text[line_start:line_end].strip() == sp.text else None
        if hg:
            heading = hg
            heading_tests.setdefault(hg, set())
            prev_lead = []
            out.append(sc)
            continue
        if heading and heading != "_other" and BULLET_RE.match(whole_line):
            heading_tests[heading].update(m.term_id for m in ms)
        g = _group_claim(sp.text, heading, [(m.span.start - sp.start, m.span.end - sp.start) for m in ms])
        if g:
            sc.claims.append(Claim(kind="group", span=sp, group=g, group_tests=set(heading_tests.get(g, set()))))
        # ---- compound mentions: "Bilirubin (Total: 1.6, Direct: 0.4, Indirect: 1.2 mg/dL)" names three tests
        compounds, comp_claims, suppressed = [], [], set()
        for m in ms:
            cm, cc = _compound(text, m)
            if cm:
                compounds += cm
                comp_claims += cc
                suppressed.add((m.span.start, m.span.end))
        sc.mentions = ms + compounds                     # every named test counts as mentioned (omissions)
        # ---- the bullet's **bold lead** (computed on the first sentence of each line)
        line_key = line_start
        if line_key != bullet_line:
            bullet_line, lead_mentions, lead_done = line_key, [], False
            lm = LEAD_RE.match(whole_line)
            if BULLET_RE.match(whole_line) and lm:
                la, lb = line_start + lm.start("lead"), line_start + lm.end("lead")
                lead_mentions = [m for m in mentions_all + compounds if la <= m.span.start and m.span.end <= lb
                                 and m.span.text.lower() not in GENERIC_MENTIONS
                                 and (m.span.start, m.span.end) not in suppressed]
                lead_status = LEAD_STATUS_RE.match(text[la:lb].strip())
                lead_word = (lead_status, la) if lead_status else None
            else:
                lead_word = None
        ms = [m for m in ms if (m.span.start, m.span.end) not in suppressed]
        # "HDL (good cholesterol)": a parenthetical gloss after a test is not another test
        # (a bracket listing tests - "white blood cells (Neutrophils, Lymphocytes, etc.)" - is not a gloss)
        gloss = [(m.span.end + g.start(1), m.span.end + g.end(1)) for m in ms
                 if m.span.text.lower() not in GENERIC_MENTIONS
                 for g in [re.match(r"\s*\(([^()\d,]{2,40})\)", text[m.span.end:])] if g]
        ms = [m for m in ms if not any(a <= m.span.start and m.span.end <= b for a, b in gloss)]
        sc.mentions = [m for m in sc.mentions if not any(a <= m.span.start and m.span.end <= b for a, b in gloss)]
        per_mention: list[list[Claim]] = []
        for i, m in enumerate(ms):
            end = ms[i + 1].span.start if i + 1 < len(ms) else sp.end
            if compounds:
                nxt = [c.span.start for c in compounds if c.span.start > m.span.end]
                end = min([end] + nxt)
            per_mention.append(_segment_claims(text, m, m.span.end, end, sp.start))
        # "Like ALT, this value is within range (7-56)": a test named only for comparison is
        # not the subject; its ranges and statuses belong to the bullet's lead test
        for i, m in enumerate(ms):
            if lead_mentions and m not in lead_mentions and COMPARISON_BEFORE.search(text[sp.start:m.span.start]):
                moved = []
                if len(lead_mentions) == 1:
                    subj = lead_mentions[0]
                    moved = [Claim(kind=c.kind, span=c.span, status=c.status, range=c.range, term_id=subj.term_id,
                                   term=subj.term, mention=subj.span) for c in per_mention[i] if c.kind in ("range", "status")]
                per_mention[i] = moved
        # a doctor deferral or a suggested test ("your doctor may order a TSH test") makes no test claims
        if DEFERRAL.search(text[sp.start:sp.end]) or SUGGEST_TEST.search(text[sp.start:sp.end]):
            per_mention = [[] for _ in ms]
            comp_claims = []
        # "very low TSH and elevated Free T4": an adjective right before a test belongs to that test
        pre_adj = []
        for i, m in enumerate(ms):
            left_a = ms[i - 1].span.end if i else sp.start
            am = PRE_ADJ.search(text[left_a:m.span.start])
            if am:
                pre_adj.append((i, left_a + am.start("adj"), left_a + am.end("adj"), am["adj"]))
        for i, aa, ab, _ in pre_adj:                 # first take each adjective away from the previous test ...
            if i:
                per_mention[i - 1] = [c for c in per_mention[i - 1]
                                      if not (c.kind == "status" and aa <= c.span.start < ab + 1)]
        for i, aa, ab, adj in pre_adj:               # ... then give it to the test right after it
            if not any(c.kind == "status" for c in per_mention[i]):
                st = "LOW" if LOW_RE.match(adj.split()[-1]) else "HIGH"
                per_mention[i].append(Claim(kind="status", span=_sp(text, aa, ab), status=st, term_id=ms[i].term_id,
                                            term=ms[i].term, mention=ms[i].span))
        # "Your MCV and MCH are normal": a mention followed only by a conjunction shares the next one's status
        for i in range(len(ms) - 2, -1, -1):
            gap = text[ms[i].span.end:ms[i + 1].span.start]
            if not per_mention[i] and re.fullmatch(r"[\s,/&]*(?:and|or|&)?[\s,/]*", gap, re.I):
                for c in per_mention[i + 1]:
                    if c.kind == "status":
                        per_mention[i].append(Claim(kind="status", span=c.span, status=c.status, term_id=ms[i].term_id,
                                                    term=ms[i].term, mention=ms[i].span))
        sc.claims.extend(comp_claims)
        # ---- subject binding
        value_subjects = [ms[i] for i, cl in enumerate(per_mention) if any(c.kind == "value" for c in cl)
                          and ms[i].span.text.lower() not in GENERIC_MENTIONS]
        subjects = {m.term_id: m for m in (lead_mentions + value_subjects + compounds)}
        for i, m in enumerate(ms):
            if m.span.text.lower() not in GENERIC_MENTIONS or not subjects:
                continue
            moved = []                                    # 'red blood cells ... which is normal': the subject's status
            for c in per_mention[i]:
                if c.kind == "status" and len(subjects) == 1:
                    subj = next(iter(subjects.values()))
                    moved.append(Claim(kind="status", span=c.span, status=c.status, term_id=subj.term_id,
                                       term=subj.term, mention=subj.span))
            per_mention[i] = [c for c in per_mention[i] if c.kind != "status"] + moved
        for cl in per_mention:
            sc.claims.extend(cl)
        # the status word in the bullet lead itself ("**Low Hemoglobin (11.2), Total RBC (4.10) ...**")
        if lead_word and not lead_done and any(sp.start <= m.span.start < sp.end for m in lead_mentions):
            lead_done = True
            word, la = lead_word
            st = LEAD_STATUS[word.group(1).lower().split()[0]]
            wa = la + (len(text[la:]) - len(text[la:].lstrip())) + word.start(1)
            for m in lead_mentions:
                if not any(c.kind == "status" and c.term_id == m.term_id for c in sc.claims):
                    sc.claims.append(Claim(kind="status", span=_sp(text, wa, wa + len(word.group(1))), status=st,
                                           term_id=m.term_id, term=m.term, mention=m.span))
        # later sentences of the same bullet that name no test: the lead's subject carries over (weak)
        elif lead_mentions and not [m for m in ms if m.span.text.lower() not in GENERIC_MENTIONS]:
            found = _find_statuses(text, sp.start, sp.end)
            if found:
                st, fa, fb = min(found, key=lambda x: x[1])
                for m in lead_mentions:
                    sc.claims.append(Claim(kind="status", span=_sp(text, fa, fb), status=st, term_id=m.term_id,
                                           term=m.term, mention=m.span, weak=True))
        named = [m for m in ms if m.span.text.lower() not in GENERIC_MENTIONS] or compounds
        if lead_mentions:
            prev_lead = lead_mentions
        # topic: the one test a heading or a bullet names; later sentences that name no test may refer to it
        is_heading_line = bool(re.match(r"^\s*#{1,6}\s", whole_line))
        tests_here = {m.term_id: m for m in named}
        if (is_heading_line or BULLET_RE.match(whole_line)) and len(tests_here) == 1:
            topic = next(iter(tests_here.values()))
        elif is_heading_line:
            topic = None
        if not named and topic is not None:
            stext0 = text[sp.start:sp.end]
            cm2 = CUTOFF_RE.match(stext0)
            if cm2:                                    # "- Under 12: Deficient." under a "Vitamin B12" heading
                cat = re.sub(r"\s+", " ", cm2["cat"].lower())
                r = parse_range(cm2["range"])          # the bullet's shape already says this is a range
                if r.kind in ("interval", "upper", "lower"):
                    ra, rb = sp.start + cm2.start("range"), sp.start + cm2.end("range")
                    sc.claims.append(Claim(kind="range", span=_sp(text, ra, rb), range=r, category=cat,
                                           term_id=topic.term_id, term=topic.term, mention=topic.span))
            elif _head_noun_ref(stext0, topic.term):       # "A standard ratio is under 0.50" after "cardiothoracic ratio"
                sc.claims.extend(_segment_claims(text, topic, sp.start, sp.end, sp.start))
            elif TOPIC_REF.search(stext0) and not lead_mentions:   # e.g. "Your result is on the high side."
                found = _find_statuses(text, sp.start, sp.end)
                if found:
                    st, fa, fb = min(found, key=lambda x: x[1])
                    sc.claims.append(Claim(kind="status", span=_sp(text, fa, fb), status=st, term_id=topic.term_id,
                                           term=topic.term, mention=topic.span, weak=True))
        # "* Borderline high is 200 to 239." under a "**High Total Cholesterol (224)**" bullet
        cm = CATEGORY_RE.match(text[sp.start:sp.end]) if not named else None
        if cm:
            subjects_c = lead_mentions or prev_lead
            cat = re.sub(r"[\s-]+", " ", cm["cat"].lower())
            if len(subjects_c) == 1:
                for c in _segment_claims(text, subjects_c[0], sp.start + cm.end(), sp.end, sp.start):
                    if c.kind == "range":
                        c.category = cat
                        sc.claims.append(c)
            elif cat not in NORMAL_CATEGORIES:
                pseudo = Mention(term_id="", term="", span=sp)
                for c in _segment_claims(text, pseudo, sp.start + cm.end(), sp.end, sp.start):
                    if c.kind == "range":
                        c.category, c.term_id, c.term, c.mention = cat, None, None, None
                        sc.claims.append(c)
            sc.claims = [c for c in sc.claims if not (c.kind == "status" and c.weak)]   # the category word isn't a status
        # e.g. "based on typical lab cut-offs", "standard ranges usually say ..."
        om = OUTSIDE_RANGES.search(text[sp.start:sp.end])
        if om:
            sc.claims.append(Claim(kind="outside_range", span=sp, detail=om.group(0)))
        # "The **72 U/L** value is fine": the bold value identifies the report row
        if not named and not lead_mentions and not cm:
            raw_sentence = original[sp.start:sp.end]
            bvs = list(BOLD_VALUE.finditer(raw_sentence))
            found = _find_statuses(text, sp.start, sp.end)
            if len(bvs) == 1 and found:
                st, fa, fb = min(found, key=lambda x: x[1])
                sc.claims.append(Claim(kind="status", span=_sp(text, fa, fb), status=st, weak=True,
                                       anchor_value=parse_number(bvs[0]["num"])))
        # "has an H flag next to X, but ... within the range": a claim that the lab's flag disagrees with the range
        if FLAG_MISMATCH_RE.search(sp.text):
            targets = [m for m in ms if m.span.text.lower() not in GENERIC_MENTIONS] or lead_mentions
            seen_t = set()
            sc.claims = [c for c in sc.claims if c.kind != "status"]          # its status words describe the flag
            for m in targets:
                if m.term_id not in seen_t:
                    seen_t.add(m.term_id)
                    sc.claims.append(Claim(kind="flag", span=sp, term_id=m.term_id, term=m.term, mention=m.span))
        # ---- quotes: "...", lines after "Your report says:", "Impression: ..." lines
        stext = text[sp.start:sp.end]
        is_bullet = bool(BULLET_RE.match(whole_line))
        qspans = [(sp.start + q.start(1), sp.start + q.end(1)) for q in QUOTE_RE.finditer(stext)]
        if (quote_mode and is_bullet) or IMPRESSION_LINE.match(stext):
            qspans.append((sp.start, sp.end))
        for qa, qb in qspans:
            sc.claims.append(Claim(kind="quote", span=_sp(text, qa, qb), detail=text[qa:qb]))
        if QUOTE_INTRO.search(stext):
            quote_mode = True
        elif not is_bullet and not qspans:
            quote_mode = False
        whole_quote = any(qb - qa >= 0.8 * (sp.end - sp.start) for qa, qb in qspans)
        # ---- certainty: a finding the report hedges, stated here as certain
        if not whole_quote:
            outside = stext
            for qa, qb in qspans:
                outside = outside[:qa - sp.start] + " " * (qb - qa) + outside[qb - sp.start:]
            cands = _certainty_candidate(outside, load_glossary())
            if cands:
                sc.claims.append(Claim(kind="certainty", span=sp, detail=",".join(cands)))
        for cat, why in find_oversteps(text[sp.start:sp.end],
                                       [(m.span.start - sp.start, m.span.end - sp.start) for m in sc.mentions]):
            sc.claims.append(Claim(kind="overstep", span=sp, overstep=cat, detail=why))
        _apply_never_claim(sc, never_claim_type(stext, whole_line), stext, [])
        if in_possibility_list:                    # "Common causes include:" -> these items are possibilities
            sc.claims = [c for c in sc.claims if c.kind not in ("value", "range", "status", "group")]
        if in_cause_list:                          # every item of a cause list is part of the same interpretation
            sc.claims = [c for c in sc.claims if c.kind != "overstep"]
            sc.claims.append(Claim(kind="overstep", span=sp, overstep="cause_list",
                                   detail=text[sp.start:sp.end].strip()))
        out.append(sc)
    return out
