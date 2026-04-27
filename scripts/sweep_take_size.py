"""
Sweep take_size on z_take.py keeping z_thresh=1.0 and 4-day means fixed.

Tests whether the strategy is throughput-bound (larger take → better) or
position-cap-bound (smaller take → better, leaves room for adverse moves).

Usage: python scripts/sweep_take_size.py
"""
import re
import subprocess
from pathlib import Path
from statistics import mean as stat_mean

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
STRAT = REPO / "strategies" / "round4" / "z_take.py"
DAY_KEYS = [("round3", 0), ("round4", 1), ("round4", 2), ("round4", 3)]
TAKE_GRID = list(range(10, 21))


def patch_take(src: str, take: int) -> str:
    return re.sub(r'("take_size"\s*:\s*)\d+', rf'\g<1>{take}', src)


def run_day(dataset: str, day: int) -> float:
    r = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--",
         "--trader", str(STRAT), "--dataset", dataset,
         f"--day={day}", "--queue-penetration", "1.0",
         "--products", "summary", "--artifact-mode", "none"],
        capture_output=True, text=True, timeout=240, cwd=str(BT_DIR),
    )
    for line in r.stdout.splitlines():
        if line.startswith("D"):
            parts = line.split()
            if len(parts) >= 5:
                try:
                    return float(parts[4])
                except ValueError:
                    pass
    return 0.0


def main() -> None:
    original = STRAT.read_text()
    backup = STRAT.with_suffix(".py.tsbak")
    backup.write_text(original)

    results = {}
    try:
        for take in TAKE_GRID:
            STRAT.write_text(patch_take(original, take))
            per_day = [run_day(ds, d) for ds, d in DAY_KEYS]
            results[take] = per_day
            mn = stat_mean(per_day)
            mi = min(per_day)
            d0, d1, d2, d3 = per_day
            print(f"take={take:>3}  d0={d0:>9,.0f}  d1={d1:>9,.0f}  "
                  f"d2={d2:>9,.0f}  d3={d3:>9,.0f}  mean={mn:>9,.0f}  "
                  f"min={mi:>9,.0f}  m+m={mn+mi:>10,.0f}", flush=True)
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)

    rows = [(t, stat_mean(p), min(p), stat_mean(p) + min(p)) for t, p in results.items()]
    by_mean = sorted(rows, key=lambda r: r[1], reverse=True)
    by_score = sorted(rows, key=lambda r: r[3], reverse=True)

    print()
    print("=" * 80)
    print(f"Sorted by MEAN")
    print("-" * 80)
    print(f"{'take':>5}  {'mean':>10}  {'min':>10}  {'mean+min':>11}")
    for i, (t, mn, mi, ms) in enumerate(by_mean):
        marker = "  ← best mean" if i == 0 else ""
        print(f"{t:>5}  {mn:>10,.0f}  {mi:>10,.0f}  {ms:>11,.0f}{marker}")

    print()
    print("=" * 80)
    print(f"Sorted by MEAN+MIN")
    print("-" * 80)
    print(f"{'take':>5}  {'mean':>10}  {'min':>10}  {'mean+min':>11}")
    for i, (t, mn, mi, ms) in enumerate(by_score):
        marker = "  ← best mean+min" if i == 0 else ""
        print(f"{t:>5}  {mn:>10,.0f}  {mi:>10,.0f}  {ms:>11,.0f}{marker}")


if __name__ == "__main__":
    main()
