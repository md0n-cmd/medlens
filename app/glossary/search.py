"""Glossary term search: exact > prefix > substring > fuzzy (difflib). Never invents definitions."""
from __future__ import annotations

import difflib

from app.glossary.matcher import load_glossary

MAX_Q = 60
RANK = {"exact": 0, "prefix": 1, "substring": 2, "fuzzy": 3}


def search_terms(q: str, limit: int = 10) -> list[dict]:
    q = q.strip().lower()[:MAX_Q]
    best: dict[str, tuple[int, float, str]] = {}
    names: dict[str, str] = {}
    for tid, e in load_glossary().items():
        for name in [e["term"], *e.get("aliases", [])]:
            n = name.lower()
            names.setdefault(n, tid)
            if n == q:
                mt = "exact"
            elif n.startswith(q):
                mt = "prefix"
            elif q in n:
                mt = "substring"
            else:
                continue
            key = (RANK[mt], len(n), mt)
            if tid not in best or key < best[tid]:
                best[tid] = key
    if len(best) < limit:
        for n in difflib.get_close_matches(q, list(names), n=limit, cutoff=0.75):
            tid = names[n]
            best.setdefault(tid, (RANK["fuzzy"], 1 - difflib.SequenceMatcher(None, q, n).ratio(), "fuzzy"))
    gl = load_glossary()
    ordered = sorted(best.items(), key=lambda kv: (kv[1][0], kv[1][1], gl[kv[0]]["term"]))[:limit]
    return [{"id": tid, "term": gl[tid]["term"], "aliases": gl[tid].get("aliases", []),
             "category": gl[tid]["category"], "plain_explanation": gl[tid]["plain_explanation"],
             "match_type": key[2]} for tid, key in ordered]
