# MedLens Verify benchmark

Real chatbot answers about **synthetic** MedLens samples (`samples/ai/*__chatbot_real.txt`), with hand-checked labels in `gold.json`.

* `python scripts/benchmark.py`: per-case table and totals (correct / false alarms / misses).
* `tests/test_benchmark.py` fails if there is **any false alarm**, or if `correct` drops below the best score in `score.json` (a ratchet). Raise the record with `python scripts/benchmark.py --record "<what changed>"`.

## Held-out answers (not for tuning)

Held-out answers go in `samples/ai/` with labels in `heldout.json` (same format as `gold.json`). They are scored with `python scripts/benchmark.py --heldout`, and every run is appended to `heldout_score.json`.

Protocol, to keep the score honest:

1. Score them **once**, with the build as it is when they arrive, and record that score in the commit that adds them.
2. Don't change rules while looking at held-out failures. Fix a failure only with a general rule that is also motivated by, and tested on, non-held-out text, and document the rule in `docs/ALGORITHM.md` §9.6.
3. Held-out results never gate the build and never enter the ratchet in `score.json`.

## Round 1 (done)

`heldout.json` (3 answers about samples 04, 05, 06) scored **26/63, 18 false alarms** on the unchanged build (commit `aa1567d`, first entry in `heldout_score.json`). Rules were then tuned on it, so it is **training data now**. `tests/test_benchmark.py` ratchets it (`score.json` → `sets.heldout_round1`) and requires 0 false alarms on it. For round 2, put new answers in a new file (e.g. `heldout_round2.json`) and follow steps 1–3 above.
