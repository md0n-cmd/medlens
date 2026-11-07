"""Both benchmark scores + changed cases vs a saved state (for commit messages).

    python scripts/bench_compare.py save <state.json>      # remember current outcomes
    python scripts/bench_compare.py diff <state.json>      # print before/after for main + held-out (read-only)
    python scripts/bench_compare.py commit <state.json>    # print before/after, then save the new state
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark  # noqa: E402


def snapshot():
    out = {}
    for name, path in (("main", benchmark.GOLD), ("held-out r1", benchmark.SETS["heldout_round1"]),
                       ("held-out r2", benchmark.SETS["heldout_round2"])):
        sc = benchmark.run(path)
        cats = {c: f"{sum(r.outcome == 'correct' for r in sc.rows if r.category == c)}/"
                   f"{sum(r.category == c for r in sc.rows)}" for c in sorted({r.category for r in sc.rows})}
        out[name] = {"correct": sc.correct, "total": sc.total, "false_alarms": sc.false_alarms, "misses": sc.misses,
                     "cats": cats,
                     "outcomes": {r.file + "|" + r.what: [r.outcome, r.got] for r in sc.rows}}
    return out


def main():
    mode, state = sys.argv[1], Path(sys.argv[2])
    now = snapshot()
    if mode in ("diff", "commit"):
        before = json.loads(state.read_text())
        print(f"{'':12} {'before':>24} {'after':>24}   after by category")
        for name in now:
            b, a = before.get(name), now[name]
            fmt = lambda d: f"{d['correct']}/{d['total']} FA {d['false_alarms']} miss {d['misses']}" if d else "-"  # noqa: E731
            cats = ", ".join(f"{c} {v}" for c, v in a.get("cats", {}).items()) if len(a.get("cats", {})) > 1 else ""
            print(f"{name:12} {fmt(b):>24} {fmt(a):>24}   {cats}")
        for name in now:
            if name not in before:
                continue
            changed = [(k, before[name]["outcomes"].get(k, ["?", "?"]), v) for k, v in now[name]["outcomes"].items()
                       if before[name]["outcomes"].get(k, ["?"])[0] != v[0]]
            if changed:
                print(f"\n{name} changed cases:")
                for k, b, a in changed:
                    f, what = k.split("|", 1)
                    print(f"  {b[0]:11} -> {a[0]:11} {f.split('__')[0]:14} got {a[1][:20]:20} | {what[:60]}")
    if mode in ("save", "commit"):
        state.write_text(json.dumps(now))


if __name__ == "__main__":
    main()
