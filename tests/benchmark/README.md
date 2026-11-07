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

`heldout.json` (3 answers about samples 04, 05, 06) scored **26/63, 18 false alarms** on the unchanged build (commit `aa1567d`, first entry in `heldout_score.json`). Rules were then tuned on it, so it is **training data now**. `tests/test_benchmark.py` ratchets it (`score.json` → `sets.heldout_round1`) and requires 0 false alarms on it. 

## Round 2 (done)

`heldout2.json` (5 answers from two chatbots about samples 08, 09, 10; answers in `samples/ai/heldout2/`) scored **38/73, 21 false alarms, 14 misses (labs 28/43, imaging 10/30)** on the unchanged build `4c71b5e` (commit `483d291`, `heldout2_score.json`). Rules were then tuned on it (`docs/ALGORITHM.md` §9.8), so it is **training data now**: ratcheted in `score.json` → `sets.heldout_round2`, 0 false alarms required, and its sentences are in the anti-special-casing guard.

* `python scripts/benchmark.py`: main table, then every set with labs vs imaging. `--set NAME --brief` scores one set (`main`, `heldout_round1`, `heldout_round2`). `--record "<note>"` ratchets all three.
* Omission checks: one check per `expect_omissions` name (substring match); any other omission is a false alarm.

## Round 3 (needed)

Every set so far has been tuned on. The next honest number needs **fresh answers nobody has looked at while changing rules**: new chatbot answers, ideally about new report types too. Add them as `heldout3.json` + `samples/ai/heldout3/`, score once on arrival with `--set`, and commit that number before any change.
