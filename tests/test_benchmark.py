"""Benchmark gate: real chatbot answers about SYNTHETIC samples vs hand-checked gold labels.

* false alarms (MedLens wrongly accusing the AI) must be 0 - hard requirement
* correct must never drop below the best score recorded in tests/benchmark/score.json (a ratchet);
  raise the record with:  python scripts/benchmark.py --record "<what changed>"
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark  # noqa: E402

SCORE = benchmark.run()
BEST = json.loads((ROOT / "tests" / "benchmark" / "score.json").read_text())


def test_no_false_alarms():
    alarms = [r for r in SCORE.rows if r.outcome == "false alarm"]
    assert not alarms, "\n" + "\n".join(f"{r.file}: got {r.got}, expected {r.expected}: {r.what}" for r in alarms)


def test_score_never_drops_below_best_recorded():
    assert SCORE.correct >= BEST["best_correct"], benchmark.table(SCORE)


def test_every_case_finds_its_sentence():
    assert not [r for r in SCORE.rows if r.got == "(sentence not found)"]


def test_gold_files_are_synthetic_samples():
    gold = json.loads((ROOT / "tests" / "benchmark" / "gold.json").read_text())
    for f in gold["files"]:
        assert "SYNTHETIC SAMPLE" in (ROOT / f["report"]).read_text()
        assert (ROOT / f["ai"]).name.endswith("__chatbot_real.txt")
