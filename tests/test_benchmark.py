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


def test_rules_do_not_special_case_benchmark_sentences():
    """Overfitting guard: no gold `find` fragment (3+ words) may appear literally in the Verify rules or lexicon."""
    source = " ".join(p.read_text().lower() for p in (ROOT / "app" / "verify").glob("*.py"))
    files = []
    for name in ("gold.json", "heldout.json"):
        files += json.loads((ROOT / "tests" / "benchmark" / name).read_text())["files"]
    for f in files:
        for c in f["cases"]:
            frag = " ".join(c["find"].lower().split())
            if len(frag.split()) >= 3:
                assert frag not in source, f"rule source contains a benchmark sentence: {frag!r}"


HELDOUT = benchmark.run(benchmark.HELDOUT)


def test_heldout_round1_is_now_training_no_false_alarms():
    """Held-out round 1 was scored once (26/63) before tuning; rules were then tuned on it, so it is training
    data now and is ratcheted like the main set. A NEW held-out set is needed for the next honest number."""
    alarms = [r for r in HELDOUT.rows if r.outcome == "false alarm"]
    assert not alarms, "\n" + "\n".join(f"{r.file}: got {r.got}, expected {r.expected}: {r.what}" for r in alarms)


def test_heldout_round1_score_never_drops_below_best_recorded():
    best = BEST["sets"]["heldout_round1"]["best_correct"]
    assert HELDOUT.correct >= best, benchmark.table(HELDOUT)
