"""MedLens Verify benchmark: real chatbot answers about SYNTHETIC samples vs hand-checked expectations.

    python scripts/benchmark.py            # per-case table + totals
    python scripts/benchmark.py --brief    # totals only
    python scripts/benchmark.py --record "<what changed>"   # raise the ratchet in score.json if improved
    python scripts/benchmark.py --heldout  # score tests/benchmark/heldout.json separately (never tuned on)

Scoring (tests/benchmark/gold.json):
  * each case: the sentence containing `find` must get a label in `accept`.
      correct     - label in accept
      false alarm - label not in accept AND it is an alarm label (CONTRADICTED, RANGE_NOT_FROM_REPORT,
                    NOT_IN_REPORT, OVERSTEPS): MedLens accused the AI wrongly
      miss        - any other wrong label (e.g. NEUTRAL where OVERSTEPS was expected), or no such sentence
  * omissions, one check per file that states something to check (a non-empty `expect_omissions`
    or a `forbid_omissions`):
      expected omission not reported -> miss; forbidden or unexpected omission -> false alarm
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.verify.check import verify  # noqa: E402
from app.verify.claims import strip_markdown  # noqa: E402

GOLD = ROOT / "tests" / "benchmark" / "gold.json"
ALARMS = {"CONTRADICTED", "RANGE_NOT_FROM_REPORT", "NOT_IN_REPORT", "OVERSTEPS"}


@dataclass
class Row:
    file: str
    what: str
    expected: str
    got: str
    outcome: str            # "correct" | "false alarm" | "miss"


@dataclass
class Score:
    rows: list[Row] = field(default_factory=list)

    def count(self, outcome: str) -> int:
        return sum(r.outcome == outcome for r in self.rows)

    @property
    def correct(self) -> int:
        return self.count("correct")

    @property
    def false_alarms(self) -> int:
        return self.count("false alarm")

    @property
    def misses(self) -> int:
        return self.count("miss")

    @property
    def total(self) -> int:
        return len(self.rows)


def _norm(s: str) -> str:
    return " ".join(strip_markdown(s).split()).lower()


def find_sentence(result, find: str):
    for s in result.sentences:
        if find in s.text or _norm(find) in _norm(s.text):
            return s
    return None


def run(gold_path: Path = GOLD) -> Score:
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    score = Score()
    for f in gold["files"]:
        report = (ROOT / f["report"]).read_text(encoding="utf-8")
        ai = (ROOT / f["ai"]).read_text(encoding="utf-8")
        r = verify(report, ai)
        name = Path(f["ai"]).stem
        for c in f["cases"]:
            s = find_sentence(r, c["find"])
            got = s.label if s else "(sentence not found)"
            if got in c["accept"]:
                outcome = "correct"
            elif got in ALARMS:
                outcome = "false alarm"
            else:
                outcome = "miss"
            score.rows.append(Row(name, c["find"], "/".join(c["accept"]), got, outcome))
        expect = f.get("expect_omissions")
        forbid = f.get("forbid_omissions")
        if expect or forbid is not None:
            got_om = {o.test_name for o in r.omissions}
            bad = (got_om - set(expect or [])) | (got_om & set(forbid or []))
            missing = set(expect or []) - got_om
            outcome = "false alarm" if bad else ("miss" if missing else "correct")
            what = "omissions" + (f" (forbid {sorted(forbid)})" if forbid else "")
            score.rows.append(Row(name, what, str(sorted(expect or [])), str(sorted(got_om)), outcome))
    return score


def table(score: Score) -> str:
    lines = [f"{'file':32} {'outcome':12} {'got':24} {'expected':34} case", "-" * 140]
    for r in score.rows:
        mark = {"correct": "ok", "false alarm": "FALSE ALARM", "miss": "miss"}[r.outcome]
        lines.append(f"{r.file:32} {mark:12} {r.got[:24]:24} {r.expected[:34]:34} {r.what[:60]}")
    lines.append("-" * 140)
    lines.append(summary(score))
    return "\n".join(lines)


def summary(score: Score) -> str:
    return (f"correct {score.correct}/{score.total} | false alarms {score.false_alarms} | "
            f"misses {score.misses}")


SCORE = ROOT / "tests" / "benchmark" / "score.json"


def record(score: Score, note: str) -> bool:
    """Ratchet: store the score if it is the best so far. Returns True if it improved."""
    data = json.loads(SCORE.read_text()) if SCORE.exists() else {"best_correct": 0, "total": score.total, "history": []}
    improved = score.correct > data["best_correct"]
    if improved or not data["history"]:
        data["best_correct"] = max(data["best_correct"], score.correct)
        data["total"] = score.total
        data["history"].append({"correct": score.correct, "false_alarms": score.false_alarms,
                                "misses": score.misses, "total": score.total, "note": note})
        SCORE.write_text(json.dumps(data, indent=2) + "\n")
    return improved


HELDOUT = ROOT / "tests" / "benchmark" / "heldout.json"
HELDOUT_LOG = ROOT / "tests" / "benchmark" / "heldout_score.json"


def run_heldout() -> Optional[Score]:
    """Held-out answers: scored and logged, never used for tuning, never a build gate, never in the ratchet."""
    if not HELDOUT.exists():
        return None
    sc = run(HELDOUT)
    log = json.loads(HELDOUT_LOG.read_text()) if HELDOUT_LOG.exists() else {"history": []}
    log["history"].append({"correct": sc.correct, "false_alarms": sc.false_alarms, "misses": sc.misses,
                           "total": sc.total})
    HELDOUT_LOG.write_text(json.dumps(log, indent=2) + "\n")
    return sc


if __name__ == "__main__":
    if "--heldout" in sys.argv:
        hs = run_heldout()
        print("no tests/benchmark/heldout.json yet" if hs is None else ("HELD-OUT\n" + table(hs)))
        sys.exit(0)
    sc = run()
    print(summary(sc) if "--brief" in sys.argv else table(sc))
    if "--record" in sys.argv:
        note = sys.argv[sys.argv.index("--record") + 1] if len(sys.argv) > sys.argv.index("--record") + 1 else ""
        print("recorded new best" if record(sc, note) else "not a new best; score.json unchanged")
