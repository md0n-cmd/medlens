"""Extract lab result rows (test name, value, unit, flag, range) from report lines.

Two layouts are supported:
  1. Columnar (columns separated by 2+ spaces, tabs or '|'):
        Hemoglobin      11.2   L    g/dL     13.0 - 17.0
  2. Inline / colon:
        Hemoglobin: 11.2 g/dL (13.0-17.0)
        Hemoglobin 11.2 g/dL Ref: 13.0-17.0
All spans are absolute character offsets into the report text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.models import Span
from app.parser.lines import Line
from app.parser.ranges import NUM, is_qualitative_word, parse_range

COL_SEP_RE = re.compile(r"\s{2,}|\s*\|\s*")
VALUE_HEAD_RE = re.compile(rf"^(?P<cmp><=|>=|≤|≥|<|>)?\s*(?P<num>{NUM})(?![\d/])")
FLAG_TOKEN = r"(?:\(?(?:HH|LL|H|L|High|Low|Hi|Lo|Critical|Abnormal|A)\)?|\*{1,2}|↑|↓)"
FLAG_ONLY_RE = re.compile(rf"^{FLAG_TOKEN}$", re.I)
FLAG_PREFIX_RE = re.compile(rf"^(?P<flag>{FLAG_TOKEN})(?=\s|$)", re.I)
FLAG_SUFFIX_RE = re.compile(rf"(?:^|\s)(?P<flag>{FLAG_TOKEN})$", re.I)
UNIT_RE = re.compile(
    r"^(?:[x×]\s*10\s*\^?\s*\d+\s*)?(?:10\^\d+)?[A-Za-zµμ%/³²\^\d\.\-\*]*[A-Za-zµμ%³²/]"
    r"[A-Za-zµμ%/³²\^\d\.\-]*(?:\s(?:1st\s)?[A-Za-z/]+)?$"
)
INLINE_RANGE_RE = re.compile(
    r"(?:\((?P<paren>[^()]*\d[^()]*|[^()]*(?:negative|positive|reactive|absent|nil)[^()]*)\)|"
    r"\[(?P<brack>[^\[\]]+)\]|"
    r"(?:bio\.?\s*)?(?:ref(?:erence)?\.?\s*(?:range|interval)?|normal(?:\s+range)?|range)\s*[:\-=]\s*(?P<labeled>.+)$)",
    re.I,
)
NAME_RE = re.compile(r"[A-Za-z]")

# Lines whose "name" is patient metadata, not a test.
METADATA_NAMES = re.compile(
    r"^(age|sex|gender|age\s*/\s*sex|name|patient(\s+name)?|date|time|sample|specimen|ref(\.|erred)?(\s+by)?|"
    r"dr\.?|doctor|lab(\s+no\.?)?|uhid|mrn|id|report(ed)?(\s+on)?|collected(\s+on)?|received(\s+on)?|"
    r"registered(\s+on)?|page|phone|mobile|address|reg(\.|istration)?\s*no\.?|sample\s+id|bill\s+no\.?|"
    r"printed(\s+on)?|visit|barcode|accession|location|ward|bed)\b",
    re.I,
)


@dataclass
class RawRow:
    test_name: str
    test_span: Span
    value_raw: str
    value_span: Span
    value_num: Optional[float] = None
    value_comparator: Optional[str] = None
    value_qualitative: Optional[str] = None
    unit: Optional[str] = None
    unit_span: Optional[Span] = None
    flag_raw: Optional[str] = None
    flag_span: Optional[Span] = None
    range_raw: Optional[str] = None
    range_span: Optional[Span] = None


def _span(base: int, s: int, e: int, text: str) -> Span:
    return Span(start=base + s, end=base + e, text=text[s:e])


def _columns(text: str) -> list[tuple[int, int]]:
    cols, pos = [], 0
    for m in COL_SEP_RE.finditer(text):
        if m.start() > pos:
            cols.append((pos, m.start()))
        pos = m.end()
    if pos < len(text):
        cols.append((pos, len(text)))
    return [(s, e) for s, e in cols if text[s:e].strip()]


def _parse_value_cell(cell: str) -> Optional[dict]:
    """Parse '11.2', '<0.01', '11.2 L', '4.5 x10^3/uL', '14.0 H g/dL', 'Negative'.
    Returns dict with relative offsets."""
    stripped = cell.rstrip()
    if is_qualitative_word(stripped):
        return {"qual": stripped, "vs": 0, "ve": len(stripped)}
    m = VALUE_HEAD_RE.match(stripped)
    if not m:
        return None
    res = {"cmp": m["cmp"], "num": m["num"], "vs": 0, "ve": m.end()}
    pos = m.end()

    def skip_ws(p: int) -> int:
        while p < len(stripped) and stripped[p].isspace():
            p += 1
        return p

    pos = skip_ws(pos)
    r = stripped[pos:]
    fm = FLAG_PREFIX_RE.match(r) or re.match(r"^(?P<flag>\*+|↑|↓)", r)
    if fm:
        res["flag"] = (pos, pos + fm.end())
        pos = skip_ws(pos + fm.end())
        r = stripped[pos:]
    if r:
        unit_text = r
        fm2 = FLAG_SUFFIX_RE.search(r)
        if fm2 and "flag" not in res and fm2.start("flag") > 0:
            res["flag"] = (pos + fm2.start("flag"), pos + fm2.end("flag"))
            unit_text = r[: fm2.start()].rstrip()
        if UNIT_RE.match(unit_text) and not re.search(r"\d+\.\d+", unit_text):
            res["unit"] = (pos, pos + len(unit_text))
        else:
            return None
    return res


def _row_from_value(line: Line, name_s: int, name_e: int, val_s: int, cell: str, pv: dict) -> RawRow:
    t, base = line.text, line.start
    name_text = t[name_s:name_e].rstrip(" :=-.")
    row = RawRow(
        test_name=name_text.strip(),
        test_span=_span(base, name_s, name_s + len(name_text), t),
        value_raw=cell[pv["vs"]:pv["ve"]],
        value_span=_span(base, val_s + pv["vs"], val_s + pv["ve"], t),
    )
    if "qual" in pv:
        row.value_qualitative = pv["qual"]
    else:
        row.value_num = float(pv["num"].replace(",", ""))
        row.value_comparator = {"≤": "<=", "≥": ">="}.get(pv["cmp"], pv["cmp"])
    if "flag" in pv:
        fs, fe = pv["flag"]
        row.flag_span = _span(base, val_s + fs, val_s + fe, t)
        row.flag_raw = row.flag_span.text.strip("()")
    if "unit" in pv:
        us, ue = pv["unit"]
        row.unit_span = _span(base, val_s + us, val_s + ue, t)
        row.unit = row.unit_span.text
    return row


def _valid_name(name: str) -> bool:
    n = name.strip(" :=-.")
    return bool(n) and bool(NAME_RE.search(n)) and not METADATA_NAMES.match(n) and len(n) <= 60


def parse_columnar(line: Line) -> Optional[RawRow]:
    t = line.text
    cols = _columns(t)
    if len(cols) < 2:
        return None
    name_s, name_e = cols[0]
    if not _valid_name(t[name_s:name_e]):
        return None
    # value = first column after the name that parses as a value
    for idx in range(1, len(cols)):
        cs, ce = cols[idx]
        pv = _parse_value_cell(t[cs:ce])
        if pv:
            break
        return None  # 2nd column must be the value; otherwise not a result row
    else:
        return None
    row = _row_from_value(line, name_s, name_e, cs, t[cs:ce], pv)
    rest = cols[idx + 1:]
    # separate flag column
    if rest and FLAG_ONLY_RE.match(t[rest[0][0]:rest[0][1]].strip()) and row.flag_raw is None:
        fs, fe = rest[0]
        row.flag_span = _span(line.start, fs, fe, t)
        row.flag_raw = row.flag_span.text.strip("()")
        rest = rest[1:]
    # find the range: try the longest suffix first (multi-range cells may contain 2+ spaces)
    range_found = False
    for i in range(len(rest)):
        for j in range(len(rest), i, -1):
            rs, re_ = rest[i][0], rest[j - 1][1]
            cand = t[rs:re_]
            if parse_range(cand).kind != "unparseable":
                row.range_raw, row.range_span = cand, _span(line.start, rs, re_, t)
                range_found = True
                break
        if range_found:
            # unit is the column(s) between value and range, if any
            if i > 0 and row.unit is None:
                us, ue = rest[0]
                row.unit_span = _span(line.start, us, ue, t)
                row.unit = row.unit_span.text
            break
    if not range_found and rest and row.unit is None:
        us, ue = rest[0]
        cand = t[us:ue]
        if UNIT_RE.match(cand.strip()):
            row.unit_span = _span(line.start, us, ue, t)
            row.unit = cand
    return row


INLINE_RE = re.compile(rf"^(?P<name>\s*[A-Za-z][^:=]*?)\s*(?:[:=]|\s(?=(?:<=|>=|≤|≥|<|>)?\s*(?:{NUM})(?:\s|$|[A-Za-z%µμ*↑↓])))\s*(?P<rest>.+)$")


def parse_inline(line: Line) -> Optional[RawRow]:
    t = line.text
    m = INLINE_RE.match(t)
    if not m or not _valid_name(m["name"]):
        return None
    rest_s = m.start("rest")
    rest = t[rest_s:]
    rng = None
    rm = INLINE_RANGE_RE.search(rest)
    value_part = rest
    if rm:
        grp = "paren" if rm["paren"] is not None else "brack" if rm["brack"] is not None else "labeled"
        rng = (rest_s + rm.start(grp), rest_s + rm.end(grp))
        value_part = rest[: rm.start()]
    pv = _parse_value_cell(value_part.strip())
    if not pv:
        return None
    lead = len(value_part) - len(value_part.lstrip())
    name_s = len(m["name"]) - len(m["name"].lstrip())
    row = _row_from_value(line, name_s, m.end("name"), rest_s + lead, value_part.strip(), pv)
    if rng:
        cand = t[rng[0]:rng[1]]
        if parse_range(cand).kind != "unparseable":
            row.range_raw, row.range_span = cand.strip(), _span(line.start, rng[0], rng[0] + len(cand.rstrip()), t)
        else:
            row.range_raw, row.range_span = cand.strip(), _span(line.start, rng[0], rng[0] + len(cand.rstrip()), t)
    return row


def parse_line(line: Line) -> Optional[RawRow]:
    if not line.text.strip():
        return None
    return parse_columnar(line) or parse_inline(line)
