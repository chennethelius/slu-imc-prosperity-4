"""
2D sweep of (STALE_LENGTH, STALE_Z_FACTOR) for the two stale-aware variants.

Stale-mode redesign: when ticks_since_fill > STALE_LENGTH with pos != 0,
the strategy multiplies z_thresh by STALE_Z_FACTOR (≤1.0, smaller =
easier trigger) and blocks any add-to-position trade. Mean reversion
still required for unwinds, just at a lower bar.

For each (length, factor) point: 4-day backtest, parse total PnL,
score by per-day mean + per-day min. Compares to no-stale references
(per_asset and combined).

Usage:  python scripts/sweep_z_take_stale.py
"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
DAY_KEYS = [("round3", 0), ("round4", 1), ("round4", 2), ("round4", 3)]

STALE_LENGTHS = [500, 1000, 2000]
STALE_Z_FACTORS = [0.0, 0.25, 0.5, 0.75, 1.0]

REF_STRATS = {
    "per_asset (static, no stale)": REPO / "strategies" / "round4" / "tmp" / "z_take_per_asset.py",
    "combined  (blend, no stale)":  REPO / "strategies" / "round4" / "tmp" / "z_take_combined.py",
}

STALE_STRATS = {
    "stale_static":   REPO / "strategies" / "round4" / "tmp" / "z_take_stale_static.py",
    "stale_combined": REPO / "strategies" / "round4" / "tmp" / "z_take_stale_combined.py",
}


def patch(src: str, length: int, factor: float) -> str:
    src = re.sub(r'(STALE_LENGTH\s*=\s*)\d+', rf'\g<1>{length}', src)
    src = re.sub(r'(STALE_Z_FACTOR\s*=\s*)[\d.]+', rf'\g<1>{factor}', src)
    return src


def run_one_day(strat: Path, dataset: str, day: int) -> float:
    r = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--",
         "--trader", str(strat), "--dataset", dataset, f"--day={day}",
         "--queue-penetration", "1.0", "--products", "summary",
         "--artifact-mode", "none"],
        capture_output=True, text=True, timeout=240, cwd=str(BT_DIR),
    )
    for line in r.stdout.splitlines():
        if line.startswith("D") and "TICKS" not in line:
            parts = line.split()
            if len(parts) >= 5:
                try:
                    return float(parts[4])
                except ValueError:
                    pass
    return 0.0


def run_4_days(strat: Path) -> list[float]:
    return [run_one_day(strat, ds, d) for ds, d in DAY_KEYS]


def fmt(x: float, w: int = 9) -> str:
    return f"{x:>{w},.0f}"


def main() -> None:
    print("=" * 100)
    print("REFERENCES (no stale logic)")
    print("=" * 100)
    refs: dict[str, list[float]] = {}
    for label, strat in REF_STRATS.items():
        per_day = run_4_days(strat)
        refs[label] = per_day
        mn, mi = sum(per_day) / 4, min(per_day)
        print(f"  {label:<32}  d0={fmt(per_day[0])} d1={fmt(per_day[1])} "
              f"d2={fmt(per_day[2])} d3={fmt(per_day[3])}  "
              f"mean={fmt(mn)} min={fmt(mi)} m+m={fmt(mn+mi,10)}")

    # results[strat_label][(length, factor)] = per_day [4]
    results: dict[str, dict[tuple[int, float], list[float]]] = {}

    backups = {}
    try:
        for label, strat in STALE_STRATS.items():
            backups[strat] = strat.read_text()
            results[label] = {}
            print()
            print("=" * 100)
            print(f"SWEEP {label}")
            print("=" * 100)
            for length in STALE_LENGTHS:
                for factor in STALE_Z_FACTORS:
                    strat.write_text(patch(backups[strat], length, factor))
                    per_day = run_4_days(strat)
                    results[label][(length, factor)] = per_day
                    mn, mi = sum(per_day) / 4, min(per_day)
                    print(f"  L={length:>5}  f={factor:>4}  "
                          f"d0={fmt(per_day[0])} d1={fmt(per_day[1])} "
                          f"d2={fmt(per_day[2])} d3={fmt(per_day[3])}  "
                          f"mean={fmt(mn)} min={fmt(mi)} m+m={fmt(mn+mi,10)}",
                          flush=True)
    finally:
        for strat, content in backups.items():
            strat.write_text(content)

    # ===== Per-variant 2D table =====
    pairings = [
        ("stale_static",   "per_asset (static, no stale)"),
        ("stale_combined", "combined  (blend, no stale)"),
    ]
    for stale_label, ref_label in pairings:
        ref = refs[ref_label]
        ref_mm = sum(ref) / 4 + min(ref)
        print("\n" + "=" * 100)
        print(f"{stale_label}: m+m grid (Δ vs reference {ref_label} = {ref_mm:,.0f})")
        print("=" * 100)
        # column = factor, row = length
        header = "        " + "  ".join(f"f={f:>4}" for f in STALE_Z_FACTORS)
        print(header)
        for length in STALE_LENGTHS:
            cells = []
            for factor in STALE_Z_FACTORS:
                pd = results[stale_label][(length, factor)]
                mm = sum(pd) / 4 + min(pd)
                cells.append(f"{mm-ref_mm:>+8,.0f}")
            print(f"L={length:>5}  " + "  ".join(cells))

        # best
        best = None
        best_score = -1e18
        for length in STALE_LENGTHS:
            for factor in STALE_Z_FACTORS:
                pd = results[stale_label][(length, factor)]
                mm = sum(pd) / 4 + min(pd)
                if mm > best_score:
                    best_score = mm
                    best = (length, factor, pd, mm)
        print(f"\n  best: L={best[0]}, f={best[1]}  m+m={best[3]:,.0f}  "
              f"Δ vs ref={best[3]-ref_mm:+,.0f}")
        print(f"  per-day: d0={fmt(best[2][0])} d1={fmt(best[2][1])} "
              f"d2={fmt(best[2][2])} d3={fmt(best[2][3])}")


if __name__ == "__main__":
    main()
