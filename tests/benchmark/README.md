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
