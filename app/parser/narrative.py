"""Values written in report sentences (imaging / ECG): "Heart Rate: 88 bpm.", "PR interval 160 ms.",
"Cardiothoracic ratio is about 0.55". Only glossary TEST names count; a value gets no range unless one is printed."""
from __future__ import annotations

import re
from typing import Optional

from app.glossary.matcher import find_terms, load_glossary
from app.models import Span
from app.parser.values import RawRow

AFTER_NAME = re.compile(
    r"^\s*(?::|=|\bis\b|\bwas\b|\bof\b|\bmeasures?\b|\bmeasuring\b)?\s*(?:\b(?:about|approximately|approx\.?|around)\s+|~\s*)?"
    r"(?P<num>\d+(?:\.\d+)?)(?:\s*(?P<unit>bpm|beats\s+per\s+minute|ms|msec|mm|cm|mmhg|%))?(?=\s*(?:[.,;)]|$))", re.I)


def narrative_values(line: str, base: int) -> list[RawRow]:
    gl = load_glossary()
    out: list[RawRow] = []
    for t in find_terms(line):
        if gl.get(t.term_id, {}).get("kind") != "test":
            continue
        a = t.span.start
        b = t.span.end
        m = AFTER_NAME.match(line[b:])
        if not m:
            continue
        ns, ne = b + m.start("num"), b + m.end("num")
        unit: Optional[str] = m.group("unit")
        out.append(RawRow(
            test_name=line[a:b], test_span=Span(start=base + a, end=base + b, text=line[a:b]),
            value_raw=m.group("num"), value_span=Span(start=base + ns, end=base + ne, text=line[ns:ne]),
            value_num=float(m.group("num")), unit=unit,
            unit_span=Span(start=base + b + m.start("unit"), end=base + b + m.end("unit"), text=unit) if unit else None))
    return out
