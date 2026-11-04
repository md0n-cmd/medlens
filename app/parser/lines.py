"""Split a report into lines and sections, preserving character offsets."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.models import Section

INTERPRETIVE_SECTIONS = re.compile(
    r"^(impression|conclusion|opinion|advice|remarks?|comments?|interpretation|suggestions?|"
    r"recommendations?|clinical\s+(?:notes?|correlation)|summary|diagnosis|final\s+diagnosis|note)s?$",
    re.I,
)
# A line like "IMPRESSION:" or "Impression: fatty liver" or "COMPLETE BLOOD COUNT"
KEYWORD_HEADER_RE = re.compile(r"^\s*(?P<name>[A-Za-z][A-Za-z /&()]{1,40}?)\s*:\s*(?P<rest>.*)$")
CAPS_HEADER_RE = re.compile(r"^\s*(?P<name>[A-Z][A-Z0-9 /&(),\-]{2,60}?)\s*:?\s*$")
KNOWN_HEADERS = re.compile(
    r"^(findings|impression|conclusion|opinion|advice|remarks?|comments?|interpretation|"
    r"suggestions?|recommendations?|clinical\s+history|history|technique|summary|note)$",
    re.I,
)


@dataclass
class Line:
    number: int           # 1-based
    start: int
    end: int              # exclusive, excludes newline
    text: str
    section: Optional[str] = None
    interpretive: bool = False


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")


def split_lines(text: str) -> list[Line]:
    out, pos = [], 0
    for i, raw in enumerate(text.split("\n"), start=1):
        out.append(Line(number=i, start=pos, end=pos + len(raw), text=raw))
        pos += len(raw) + 1
    return out


def header_name(line: str) -> Optional[tuple[str, bool]]:
    """Return (section name, has_inline_content) if the line starts a section."""
    m = KEYWORD_HEADER_RE.match(line)
    if m and KNOWN_HEADERS.match(m["name"].strip()):
        return m["name"].strip(), bool(m["rest"].strip())
    m = CAPS_HEADER_RE.match(line)
    if m and not re.search(r"\S\s{2,}\S", line.strip()) and sum(c.isalpha() for c in m["name"]) >= 3 and not re.search(r"\d", m["name"]):
        return m["name"].strip(), False
    return None


def segment(text: str) -> tuple[list[Line], list[Section]]:
    """Assign every line to a section. Interpretive sections (Impression, Advice,...) are flagged."""
    lines = split_lines(text)
    sections: list[Section] = []
    current: Optional[Section] = None
    for ln in lines:
        if re.match(r"^\s*(?:\*{2,}|={3,}|-{3,}).*$", ln.text) and current:
            current = None      # banner / divider lines end the current section
            continue
        h = header_name(ln.text)
        if h:
            name, _ = h
            if current:
                current.end = max(current.start, ln.start - 1)
            current = Section(name=name, start=ln.start, end=ln.end,
                              interpretive=bool(INTERPRETIVE_SECTIONS.match(name)))
            sections.append(current)
        if current:
            current.end = ln.end
            ln.section, ln.interpretive = current.name, current.interpretive
    return lines, sections
