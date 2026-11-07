"""Order-independent test-name normalisation, used when glossary ids alone don't link an AI mention to a report row.

"Cholesterol, Total" == "Total Cholesterol"; "GLUCOSE FASTING (F), PLASMA" == "Fasting Glucose";
"VLDL Cholesterol,Calculated" == "VLDL Cholesterol"; "E.S.R" == "ESR".
"""
from __future__ import annotations

import re

QUALIFIERS = {"calculated", "calc", "plasma", "serum", "f", "s", "whole", "edta", "method", "level", "levels",
              "value", "values", "test", "the", "of", "by", "your", "photometry", "spectrophotometry", "ise"}


def name_keys(name: str) -> set[frozenset]:
    """Word-set keys for a test name: the whole name, the name without its bracket, and the bracket alone."""
    variants = {name}
    m = re.match(r"^(.*?)\(([^()]*)\)(.*)$", name)
    if m:
        variants |= {m[1] + " " + m[3], m[2]}
    keys = set()
    for v in variants:
        v = re.sub(r"\b(?:[A-Za-z]\.){2,}[A-Za-z]?\.?", lambda x: x.group(0).replace(".", ""), v)   # E.S.R -> ESR
        words = [w for w in re.findall(r"[a-z0-9]+", v.lower()) if w not in QUALIFIERS]
        if words:
            keys.add(frozenset(words))
    return keys


def names_match(a: str, b: str) -> bool:
    return bool(name_keys(a) & name_keys(b))
