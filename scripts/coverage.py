"""Glossary coverage per sample: medical-looking tokens recognized vs unrecognized.

Usage:  python scripts/coverage.py [path/to/terms.json]   (default: the current glossary)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.glossary import matcher  # noqa: E402

if len(sys.argv) > 1:
    matcher.TERMS_PATH = Path(sys.argv[1])
from app.pipeline import analyze  # noqa: E402

n_entries = len(matcher.load_glossary())
print(f"Glossary entries: {n_entries}")
print(f"{'sample':28} {'recognized':>10} {'unrecognized':>12} {'coverage':>9}  unrecognized tokens")
tot_r = tot_u = 0
for p in sorted((ROOT / "samples").glob("*.txt")):
    r = analyze(p.read_text())
    rec = len({t.matched_text.lower() for t in r.terms})
    unr = [u.text for u in r.unrecognized]
    tot_r, tot_u = tot_r + rec, tot_u + len(unr)
    cov = rec / (rec + len(unr)) if rec + len(unr) else 1.0
    print(f"{p.name:28} {rec:>10} {len(unr):>12} {cov:>8.0%}  {', '.join(unr)}")
print(f"{'TOTAL':28} {tot_r:>10} {tot_u:>12} {tot_r / (tot_r + tot_u):>8.0%}")
