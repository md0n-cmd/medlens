"""Both benchmark scores + changed cases vs a saved state (for commit messages).

    python scripts/bench_compare.py save <state.json>      # remember current outcomes
    python scripts/bench_compare.py diff <state.json>      # print before/after for main + held-out, then save
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark  # noqa: E402


def snapshot():
    out = {}
    for name, path in (("main", benchmark.GOLD), ("held-out", benchmark.HELDOUT)):
        sc = benchmark.run(path)
        out[name] = {"correct": sc.correct, "total": sc.total, "false_alarms": sc.false_alarms, "misses": sc.misses,
                     "outcomes": {r.file + "|" + r.what: [r.outcome, r.got] for r in sc.rows}}
    return out


def main():
    mode, state = sys.argv[1], Path(sys.argv[2])
    now = snapshot()
    if mode == "diff":
        before = json.loads(state.read_text())
        print(f"{'':10} {'before':>22} {'after':>22}")
        for name in ("main", "held-out"):
            b, a = before[name], now[name]
            fmt = lambda d: f"{d['correct']}/{d['total']} FA {d['false_alarms']} miss {d['misses']}"  # noqa: E731
            print(f"{name:10} {fmt(b):>22} {fmt(a):>22}")
        for name in ("main", "held-out"):
            changed = [(k, before[name]["outcomes"].get(k, ["?", "?"]), v) for k, v in now[name]["outcomes"].items()
                       if before[name]["outcomes"].get(k, ["?"])[0] != v[0]]
            if changed:
                print(f"\n{name} changed cases:")
                for k, b, a in changed:
                    f, what = k.split("|", 1)
                    print(f"  {b[0]:11} -> {a[0]:11} {f.split('__')[0]:14} got {a[1][:20]:20} | {what[:60]}")
    state.write_text(json.dumps(now))


if __name__ == "__main__":
    main()
