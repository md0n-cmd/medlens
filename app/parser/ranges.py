"""Reference-range parsing and comparison.

Ranges are parsed ONLY from text supplied in the report. There are no built-in
population ranges anywhere in MedLens: if the report does not print a range,
the value is reported as `no_reference_range_supplied` and never flagged.

Comparison semantics (see docs/ALGORITHM.md):
  * `a-b`, `a to b`, `Up to b`, `≤ b`, `≥ a`  -> inclusive bounds (value == bound is within)
  * `< b`, `Less than b`, `> a`, `More than a` -> exclusive bounds (value == bound is OUT)
  * censored values (`<0.01`, `>1000`) are treated as intervals; we only report
    within/above/below when the whole interval lies on one side, otherwise
    `indeterminate`.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

from app.models import LabeledRange, ReferenceRange, Status

# ---------------------------------------------------------------- numbers
# Indian (1,50,000) and Western (150,000) grouping, decimals, leading-dot decimals.
NUM = r"(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?|\.\d+"
NUM_RE = re.compile(NUM)


def parse_number(s: str) -> float:
    return float(s.replace(",", ""))


DASH = r"\s*(?:-|–|—|to|~)\s*"

UPPER_OPS = {  # op -> inclusive?
    "<=": True, "=<": True, "≤": True, "up to": True, "upto": True, "max": True,
    "maximum": True, "<": False, "less than": False, "below": False, "under": False,
}
LOWER_OPS = {
    ">=": True, "=>": True, "≥": True, "min": True, "minimum": True, "at least": True,
    ">": False, "more than": False, "greater than": False, "above": False, "over": False,
}


def _op_alt(ops: dict) -> str:
    return "|".join(re.escape(o).replace(r"\ ", r"\s*") for o in sorted(ops, key=len, reverse=True))


UNIT_PART = r"(?P<unit>(?:[x×]\s*10\s*\^?\s*\d+\s*)?[A-Za-zµμ%/³²\^\d\.\-\*]+(?:\s[A-Za-z/]+)?)"
_TAIL = r"\s*" + UNIT_PART + r"?\s*$"

INTERVAL_RE = re.compile(rf"^(?P<lo>{NUM}){DASH}(?P<hi>{NUM}){_TAIL}", re.I)
UPPER_RE = re.compile(rf"^(?P<op>{_op_alt(UPPER_OPS)})\s*:?\s*(?P<n>{NUM}){_TAIL}", re.I)
LOWER_RE = re.compile(rf"^(?P<op>{_op_alt(LOWER_OPS)})\s*:?\s*(?P<n>{NUM}){_TAIL}", re.I)
AND_ABOVE_RE = re.compile(rf"^(?P<n>{NUM})\s*(?:and\s+above|or\s+more|or\s+above|\+)\s*" + UNIT_PART + r"?\s*$", re.I)

PREFIX_RE = re.compile(
    r"^(?:(?:bio\.?\s*)?ref(?:erence)?\.?\s*(?:range|interval|value)?s?|normal(?:\s+range)?|range)\s*[:\-=]?\s*",
    re.I,
)

# Qualitative vocabulary -> equivalence group. Only exact same-group matches are
# considered "matching"; different known groups -> differs; unknown -> indeterminate.
QUAL_GROUPS = {
    "negative": "neg", "neg": "neg", "nil": "neg", "absent": "neg", "not detected": "neg",
    "non reactive": "neg", "nonreactive": "neg", "not seen": "neg", "none seen": "neg",
    "positive": "pos", "pos": "pos", "reactive": "pos", "present": "pos", "detected": "pos",
    "trace": "trace", "+": "pos1", "++": "pos2", "+++": "pos3", "++++": "pos4",
    "1+": "pos1", "2+": "pos2", "3+": "pos3", "4+": "pos4",
    "normal": "normal", "clear": "clear", "pale yellow": "pale yellow", "straw": "straw",
    "yellow": "yellow", "dark yellow": "dark yellow", "turbid": "turbid",
    "slightly turbid": "slightly turbid", "acidic": "acidic", "alkaline": "alkaline",
}


def normalize_qual(s: str) -> str:
    s = s.strip().lower().rstrip(".")
    s = re.sub(r"[\-_]+", " ", s)
    return re.sub(r"\s+", " ", s)


def is_qualitative_word(s: str) -> bool:
    return normalize_qual(s) in QUAL_GROUPS


# Labels for sex/age-specific alternatives within one range cell.
SEX_LABELS = {"m": "male", "male": "male", "males": "male", "men": "male",
              "f": "female", "female": "female", "females": "female", "women": "female"}
LABEL_RE = re.compile(
    r"(?<![A-Za-z])(?P<label>males?|females?|men|women|adults?|child(?:ren)?|infants?|newborns?|m|f)"
    r"\s*[:\-=]?\s*(?=[<>≤≥\d.]|up|less|more)",
    re.I,
)


def _strip_wrappers(s: str) -> str:
    s = s.strip()
    while len(s) >= 2 and s[0] in "([{" and s[-1] in ")]}":
        s = s[1:-1].strip()
    s = PREFIX_RE.sub("", s, count=1).strip()
    return s.strip(" ;,")


def _clean_unit(u: Optional[str]) -> Optional[str]:
    if not u:
        return None
    u = u.strip()
    return u or None


def _parse_single(s: str, raw: str) -> ReferenceRange:
    s = _strip_wrappers(s)
    if not s:
        return ReferenceRange(raw=raw, kind="unparseable")
    m = INTERVAL_RE.match(s)
    if m:
        lo, hi = parse_number(m["lo"]), parse_number(m["hi"])
        if lo > hi:
            return ReferenceRange(raw=raw, kind="unparseable")
        return ReferenceRange(raw=raw, kind="interval", low=lo, high=hi, unit=_clean_unit(m["unit"]))
    m = UPPER_RE.match(s)
    if m:
        op = re.sub(r"\s+", " ", m["op"].lower())
        return ReferenceRange(raw=raw, kind="upper", high=parse_number(m["n"]),
                              high_inclusive=UPPER_OPS[op], unit=_clean_unit(m["unit"]))
    m = LOWER_RE.match(s)
    if m:
        op = re.sub(r"\s+", " ", m["op"].lower())
        return ReferenceRange(raw=raw, kind="lower", low=parse_number(m["n"]),
                              low_inclusive=LOWER_OPS[op], unit=_clean_unit(m["unit"]))
    m = AND_ABOVE_RE.match(s)
    if m:
        return ReferenceRange(raw=raw, kind="lower", low=parse_number(m["n"]),
                              low_inclusive=True, unit=_clean_unit(m["unit"]))
    if is_qualitative_word(s):
        return ReferenceRange(raw=raw, kind="qualitative", qualitative=s.strip())
    return ReferenceRange(raw=raw, kind="unparseable")


_N = f"(?:{NUM})"
_RANGE_BODY = (rf"(?:(?:<=|>=|≤|≥|<|>|up\s*to|upto|less\s+than|more\s+than)\s*{_N}"
               rf"|{_N}\s*(?:-|–|—|to)\s*{_N}|{_N}\s*(?:and\s+above|\+))")
COLON_SEG_RE = re.compile(
    rf"\s*[|;,]?\s*(?P<label>(?:(?!\s{{2}})[^:|;])+?)\s*:\s*(?P<range>{_RANGE_BODY})(?P<unit>\s+[A-Za-zµμ%/][^\s|;]*)?\s*",
    re.I,
)


def _parse_colon_segments(inner: str, raw: str) -> Optional[ReferenceRange]:
    """'Label: range' segments that cover the whole cell; labels may contain numbers (1-5 yrs, >60 Y)."""
    pos, alts = 0, []
    while pos < len(inner):
        m = COLON_SEG_RE.match(inner, pos)
        if not m or m.end() == pos:
            return None
        r = _parse_single(m["range"] + (m["unit"] or ""), m["range"])
        if r.kind in ("unparseable", "multi", "qualitative"):
            return None
        alts.append(LabeledRange(label=m["label"].strip(" ,;|/"), range=r))
        pos = m.end()
    return ReferenceRange(raw=raw, kind="multi", alternatives=alts) if alts else None


def parse_range(text: str) -> ReferenceRange:
    """Parse a reference-range cell exactly as printed in the report."""
    raw = text.strip()
    inner = _strip_wrappers(raw)
    if ":" in inner:
        multi = _parse_colon_segments(inner, raw)
        if multi:
            return multi
    labels = list(LABEL_RE.finditer(inner))
    if labels:
        alts: list[LabeledRange] = []
        for i, lm in enumerate(labels):
            end = labels[i + 1].start() if i + 1 < len(labels) else len(inner)
            seg = inner[lm.end():end].strip(" ;,/|")
            r = _parse_single(seg, seg)
            if r.kind in ("unparseable", "multi"):
                return ReferenceRange(raw=raw, kind="unparseable")
            alts.append(LabeledRange(label=lm["label"], range=r))
        # anything before the first label must be empty
        if inner[: labels[0].start()].strip(" ;,/|"):
            return ReferenceRange(raw=raw, kind="unparseable")
        return ReferenceRange(raw=raw, kind="multi", alternatives=alts)
    return _parse_single(inner, raw)


def looks_like_range(text: str) -> bool:
    return parse_range(text).kind != "unparseable"


# ---------------------------------------------------------------- units
def normalize_unit(u: Optional[str]) -> Optional[str]:
    if not u:
        return None
    u = u.strip().lower().replace("μ", "u").replace("µ", "u").replace("×", "x")
    u = re.sub(r"\s+", "", u)
    u = u.lstrip("x*")
    u = u.replace("mcg", "ug").replace("cu.mm", "mm3").replace("cumm", "mm3").replace("mm³", "mm3")
    u = u.replace("cmm", "mm3").replace("gm/", "g/").replace("/µl", "/ul")
    return u


def units_compatible(value_unit: Optional[str], range_unit: Optional[str]) -> bool:
    """Only a mismatch if BOTH sides state a unit and they differ after normalisation."""
    a, b = normalize_unit(value_unit), normalize_unit(range_unit)
    if not a or not b:
        return True
    return a == b


# ---------------------------------------------------------------- comparison
class Interval:
    """A (possibly half-open / unbounded) real interval."""

    def __init__(self, lo: float, lo_inc: bool, hi: float, hi_inc: bool):
        self.lo, self.lo_inc, self.hi, self.hi_inc = lo, lo_inc and math.isfinite(lo), hi, hi_inc and math.isfinite(hi)


def range_interval(r: ReferenceRange) -> Interval:
    if r.kind == "interval":
        return Interval(r.low, r.low_inclusive, r.high, r.high_inclusive)
    if r.kind == "upper":
        return Interval(-math.inf, False, r.high, r.high_inclusive)
    if r.kind == "lower":
        return Interval(r.low, r.low_inclusive, math.inf, False)
    raise ValueError(f"not numeric range: {r.kind}")


def value_interval(num: float, comparator: Optional[str]) -> Interval:
    if comparator in ("<",):
        return Interval(-math.inf, False, num, False)
    if comparator in ("<=", "≤"):
        return Interval(-math.inf, False, num, True)
    if comparator in (">",):
        return Interval(num, False, math.inf, False)
    if comparator in (">=", "≥"):
        return Interval(num, True, math.inf, False)
    return Interval(num, True, num, True)


def _subset(v: Interval, r: Interval) -> bool:
    lo_ok = r.lo < v.lo or (r.lo == v.lo and (r.lo_inc or not v.lo_inc))
    hi_ok = v.hi < r.hi or (v.hi == r.hi and (r.hi_inc or not v.hi_inc))
    return lo_ok and hi_ok


def _entirely_above(v: Interval, r: Interval) -> bool:
    if not math.isfinite(r.hi):
        return False
    return v.lo > r.hi or (v.lo == r.hi and not (v.lo_inc and r.hi_inc))


def _entirely_below(v: Interval, r: Interval) -> bool:
    if not math.isfinite(r.lo):
        return False
    return v.hi < r.lo or (v.hi == r.lo and not (v.hi_inc and r.lo_inc))


def compare_numeric(num: float, comparator: Optional[str], r: ReferenceRange,
                    num_high: Optional[float] = None) -> Status:
    v = Interval(num, True, num_high, True) if num_high is not None else value_interval(num, comparator)
    ri = range_interval(r)
    if _subset(v, ri):
        return Status.WITHIN
    if _entirely_above(v, ri):
        return Status.ABOVE
    if _entirely_below(v, ri):
        return Status.BELOW
    return Status.INDETERMINATE


def compare_qualitative(value: str, r: ReferenceRange) -> Status:
    a, b = normalize_qual(value), normalize_qual(r.qualitative or "")
    if a == b:
        return Status.WITHIN
    ga, gb = QUAL_GROUPS.get(a), QUAL_GROUPS.get(b)
    if ga and gb:
        return Status.WITHIN if ga == gb else Status.DIFFERS
    return Status.INDETERMINATE


@dataclass
class Band:
    sex: Optional[str] = None
    age_min: Optional[float] = None          # inclusive
    age_max: Optional[float] = None          # inclusive
    pregnancy: bool = False


def label_band(label: str) -> Band:
    l = label.lower()
    b = Band()
    if re.search(r"pregnan|trimester", l):
        b.pregnancy = True
    if re.search(r"\b(?:males?|men|man|boys?|m)\b", l):
        b.sex = "male"
    if re.search(r"\b(?:females?|women|woman|girls?|f)\b", l):
        b.sex = None if b.sex else "female"          # "M/F" -> no sex constraint
    if re.search(r"\badults?\b", l):
        b.age_min = 18
    if re.search(r"\b(?:child(?:ren)?|paediatric|pediatric)\b", l):
        b.age_min, b.age_max = 0, 17
    if re.search(r"\b(?:newborns?|neonates?|infants?)\b", l):
        b.age_min, b.age_max = 0, 1
    yrs = r"\s*(?:y|yr|yrs|years?)\b"
    if m := re.search(rf"(\d+)\s*(?:-|–|to)\s*(\d+){yrs}", l):
        b.age_min, b.age_max = float(m[1]), float(m[2])
    elif m := re.search(rf"(?:>=|≥|above|over)\s*(\d+){yrs}|(\d+)\s*(?:\+|and\s+above)(?:{yrs})?", l):
        b.age_min, b.age_max = float(m[1] or m[2]), None
    elif m := re.search(rf">\s*(\d+){yrs}", l):
        b.age_min, b.age_max = float(m[1]) + 1e-9, None
    elif m := re.search(rf"(?:<|below|under)\s*(\d+){yrs}", l):
        b.age_min, b.age_max = 0, float(m[1]) - 1e-9
    return b


def _band_match(b: Band, sex: Optional[str], age: Optional[float]) -> Optional[bool]:
    """True / False, or None when the header doesn't tell us."""
    if b.sex is not None:
        if sex is None:
            return None
        if b.sex != sex:
            return False
    if b.age_min is not None or b.age_max is not None:
        if age is None:
            return None
        if b.age_min is not None and age < b.age_min:
            return False
        if b.age_max is not None and age > b.age_max:
            return False
    return True


def _header_desc(sex: Optional[str], age: Optional[float]) -> str:
    parts = []
    if age is not None:
        parts.append(f"Age {age:g} Y")
    if sex:
        parts.append(f"Sex: {sex.title()}")
    return " / ".join(parts)


def select_alternative(r: ReferenceRange, patient_sex: Optional[str],
                       patient_age: Optional[float] = None) -> tuple[Optional[ReferenceRange], str]:
    """Pick one labelled range only when the report header decides it. Never guess."""
    alts = r.alternatives
    options = "; ".join(f"{a.label}: {a.range.display()}" for a in alts)
    bands = [label_band(a.label) for a in alts]
    if any(b.pregnancy for b in bands):
        return None, (f"Report lists ranges that depend on pregnancy ({options}). "
                      "MedLens cannot know which applies; ask your clinician which applies.")
    if len(alts) == 1 and bands[0].sex is None and bands[0].age_min is None and bands[0].age_max is None:
        return alts[0].range, f"Only one range (labelled '{alts[0].label}') is printed; it was used."
    matches = [_band_match(b, patient_sex, patient_age) for b in bands]
    yes = [a for a, m in zip(alts, matches) if m is True]
    if len(yes) == 1 and None not in matches:
        return yes[0].range, (f"Using the '{yes[0].label}' range printed on the report because the header says "
                              f"{_header_desc(patient_sex, patient_age)}.")
    if len(yes) > 1:
        return None, (f"Report lists overlapping ranges that could all apply ({options}); "
                      "ask your clinician which applies.")
    if len(alts) == 1 and None in matches:
        return None, (f"The only printed range is for '{alts[0].label}', and the report header doesn't say whether "
                      "that applies; ask your clinician which applies.")
    return None, f"Report lists multiple ranges ({options}); ask your clinician which applies."
