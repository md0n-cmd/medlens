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
import re
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
    category: str = "labs"  # "labs" | "imaging"


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
        cat = f.get("category") or category(report)
        for c in f["cases"]:
            s = find_sentence(r, c["find"])
            got = s.label if s else "(sentence not found)"
            if got in c["accept"]:
                outcome = "correct"
            elif got in ALARMS:
                outcome = "false alarm"
            else:
                outcome = "miss"
            score.rows.append(Row(name, c["find"], "/".join(c["accept"]), got, outcome, cat))
        expect = f.get("expect_omissions") or []
        forbid = f.get("forbid_omissions")
        got_om = sorted(o.test_name for o in r.omissions)
        hit = lambda want, have: want.lower() in have.lower()  # noqa: E731  (substring match)
        # one check per expected omission name
        for want in expect:
            ok = any(hit(want, g) for g in got_om)
            score.rows.append(Row(name, f"omission: {want}", want, str(got_om), "correct" if ok else "miss", cat))
        # an omission nobody expected is a false alarm (a row only when it happens)
        if expect:
            extra = [g for g in got_om if not any(hit(w, g) for w in expect)]
            if extra:
                score.rows.append(Row(name, "unexpected omissions", "none", str(extra), "false alarm", cat))
        if forbid is not None:
            bad = [g for g in got_om if any(hit(fb, g) for fb in forbid)]
            score.rows.append(Row(name, f"omissions (forbid {sorted(forbid)})", "none of these", str(bad),
                                  "false alarm" if bad else "correct", cat))
    return score


IMAGING = re.compile(r"\b(?:findings|impression|opinion)\b", re.I)


def category(report: str) -> str:
    """'imaging' for narrative reports (FINDINGS / IMPRESSION sections), 'labs' for tables of values."""
    from app.pipeline import analyze
    return "imaging" if any(IMAGING.search(sec.name) for sec in analyze(report).sections) else "labs"


def table(score: Score) -> str:
    lines = [f"{'file':32} {'outcome':12} {'got':24} {'expected':34} case", "-" * 140]
    for r in score.rows:
        mark = {"correct": "ok", "false alarm": "FALSE ALARM", "miss": "miss"}[r.outcome]
        lines.append(f"{r.file:32} {mark:12} {r.got[:24]:24} {r.expected[:34]:34} {r.what[:60]}")
    lines.append("-" * 140)
    lines.append(summary(score))
    return "\n".join(lines)


def summary(score: Score) -> str:
    out = (f"correct {score.correct}/{score.total} | false alarms {score.false_alarms} | "
           f"misses {score.misses}")
    out += " | " + ", ".join(f"{c} {v}" for c, v in by_category(score).items())
    return out


def by_category(score: Score) -> dict[str, str]:
    """correct/total per report category: 'labs' (tables of values) vs 'imaging' (narrative reports)."""
    return {c: f"{sum(r.outcome == 'correct' for r in score.rows if r.category == c)}/"
               f"{sum(r.category == c for r in score.rows)}" for c in sorted({r.category for r in score.rows})}


SCORE = ROOT / "tests" / "benchmark" / "score.json"


# sets that are training data (ratcheted). A set stops being held-out once rules are tuned on it.
TRAINING_SETS = {"main": "gold.json", "heldout_round1": "heldout.json", "heldout_round2": "heldout2.json"}


def record(score: Score, note: str, name: str = "main") -> bool:
    """Ratchet: store the score if it is the best so far for that set. Returns True if it improved."""
    data = json.loads(SCORE.read_text()) if SCORE.exists() else {"best_correct": 0, "total": score.total, "history": []}
    if name == "main":
        entry = data
    else:
        entry = data.setdefault("sets", {}).setdefault(name, {"best_correct": 0, "total": score.total, "history": []})
    improved = score.correct > entry["best_correct"]
    if improved or not entry["history"]:
        entry["best_correct"] = max(entry["best_correct"], score.correct)
        entry["total"] = score.total
        entry["history"].append({"correct": score.correct, "false_alarms": score.false_alarms,
                                 "misses": score.misses, "total": score.total, "by_category": by_category(score),
                                 "note": note})
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


SETS = {"main": GOLD, "heldout_round1": ROOT / "tests" / "benchmark" / "heldout.json",
        "heldout_round2": ROOT / "tests" / "benchmark" / "heldout2.json"}


if __name__ == "__main__":
    if "--set" in sys.argv:
        name = sys.argv[sys.argv.index("--set") + 1]
        sc = run(SETS[name])
        print(summary(sc) if "--brief" in sys.argv else table(sc))
        sys.exit(0)
    if "--heldout" in sys.argv:
        hs = run_heldout()
        print("no tests/benchmark/heldout.json yet" if hs is None else ("HELD-OUT\n" + table(hs)))
        sys.exit(0)
    sc = run()
    print(summary(sc) if "--brief" in sys.argv else table(sc))
    # every training set, with labs vs imaging
    others = {name: run(path) for name, path in SETS.items() if name != "main" and path.exists()}
    for name, osc in others.items():
        print(f"{name}: {summary(osc)}")
    if "--record" in sys.argv:
        note = sys.argv[sys.argv.index("--record") + 1] if len(sys.argv) > sys.argv.index("--record") + 1 else ""
        print("main:", "recorded new best" if record(sc, note) else "not a new best")
        for name, osc in others.items():
            print(f"{name}:", "recorded new best" if record(osc, note, name) else "not a new best")
