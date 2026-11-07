"""Verify counts for every AI sample: used to spot regressions on the non-benchmark samples."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.verify.check import verify  # noqa: E402

S = ROOT / "samples"
for p in sorted((S / "ai").glob("*.txt")):
    pre = p.stem.split("__")[0]
    rep = S / f"{pre}.txt"
    if not rep.exists():
        rep = next(S.glob(f"{pre}_*.txt"))
    r = verify(rep.read_text(), p.read_text())
    print(p.stem, {k: v for k, v in r.counts.items() if v})
