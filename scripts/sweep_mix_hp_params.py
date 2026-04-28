"""
Tune HP_CFG (take_max × aggr_z_thresh) inside z_take_per_asset_mix.py.

HP currently uses no_marks-style conviction logic with parameters
inherited from hybrid (which tuned take_max 80→25 to cap d2 downside on
the OLD 3-day evaluation). With our 4-day evaluation and the rest of
the mix changed (VEV_5200 now on z-take, different per-asset priors),
the optimal HP params may have shifted.

We sweep:
  take_max       : primary-layer entry size (current 25)
  aggr_z_thresh  : aggressive layer trigger threshold (current 2.5)

Per-product independence holds — HP's PnL is decoupled from the other
products in the mix — so we only report HP's per-day PnL and m+m.

Usage:  python scripts/sweep_mix_hp_params.py
"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
STRAT = REPO / "strategies" / "round4" / "z_take_per_asset_mix.py"
DAY_KEYS = [("round3", 0), ("round4", 1), ("round4", 2), ("round4", 3)]

TAKE_MAX_GRID = [15, 20, 25, 30, 40, 50]
AGGR_Z_GRID = [1.5, 2.0, 2.5, 3.0]
AGGR_MAX_GRID = [60, 90, 120]  # aggressive layer max take

# Reference: HP m+m at current HP_CFG (take_max=25, aggr_z=2.5, aggr_max=90)
REF_HP_MM = 91_453


def patch(src: str, tm: int, atz: float, amx: int) -> str:
    src = re.sub(r'("take_max"\s*:\s*)\d+', rf'\g<1>{tm}', src)
    src = re.sub(r'("aggr_z_thresh"\s*:\s*)[\d.]+', rf'\g<1>{atz}', src)
    # Match both aggr_max_take and aggr_max_take_end; rewrite to amx.
    src = re.sub(r'("aggr_max_take"\s*:\s*)\d+', rf'\g<1>{amx}', src)
    src = re.sub(r'("aggr_max_take_end"\s*:\s*)\d+', rf'\g<1>{amx}', src)
    return src


def run_one_day(dataset: str, day: int) -> tuple[float, float]:
    """Return (HP_pnl, total_pnl) for the day."""
    r = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--",
         "--trader", str(STRAT), "--dataset", dataset, f"--day={day}",
         "--queue-penetration", "1.0", "--products", "full",
         "--artifact-mode", "none"],
        capture_output=True, text=True, timeout=240, cwd=str(BT_DIR),
    )
    hp = 0.0
    total = 0.0
    in_table = False
    for line in r.stdout.splitlines():
        if line.startswith("D") and "TICKS" not in line:
            parts = line.split()
            if len(parts) >= 5:
                try:
                    total = float(parts[4])
                except ValueError:
                    pass
        if line.startswith("PRODUCT"):
            in_table = True
            continue
        if in_table:
            if not line.strip():
                break
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "HYDROGEL_PACK":
                try:
                    hp = float(parts[1])
                except ValueError:
                    pass
    return hp, total


def fmt(x: float, w: int = 9) -> str:
    return f"{x:>{w},.0f}"


def main() -> None:
    original = STRAT.read_text()
    backup = STRAT.with_suffix(".py.hpcfg_bak")
    backup.write_text(original)

    n_runs = len(TAKE_MAX_GRID) * len(AGGR_Z_GRID) * len(AGGR_MAX_GRID) * 4
    print(f"Sweeping take_max ∈ {TAKE_MAX_GRID}")
    print(f"     aggr_z_thresh ∈ {AGGR_Z_GRID}")
    print(f"     aggr_max_take ∈ {AGGR_MAX_GRID}")
    print(f"Total: {len(TAKE_MAX_GRID)} × {len(AGGR_Z_GRID)} × {len(AGGR_MAX_GRID)} "
          f"× 4 days = {n_runs} backtests\n")

    # results[(tm, atz, amx)] = (hp_per_day [4], total_per_day [4])
    results: dict[tuple[int, float, int], tuple[list[float], list[float]]] = {}

    try:
        idx = 0
        total_combos = len(TAKE_MAX_GRID) * len(AGGR_Z_GRID) * len(AGGR_MAX_GRID)
        for tm in TAKE_MAX_GRID:
            for atz in AGGR_Z_GRID:
                for amx in AGGR_MAX_GRID:
                    idx += 1
                    STRAT.write_text(patch(original, tm, atz, amx))
                    hp_per_day = [0.0] * 4
                    total_per_day = [0.0] * 4
                    for d, (ds, day) in enumerate(DAY_KEYS):
                        hp, tot = run_one_day(ds, day)
                        hp_per_day[d] = hp
                        total_per_day[d] = tot
                    results[(tm, atz, amx)] = (hp_per_day, total_per_day)
                    hp_mm = sum(hp_per_day) / 4 + min(hp_per_day)
                    tot_mm = sum(total_per_day) / 4 + min(total_per_day)
                    print(f"[{idx:>3}/{total_combos}] tm={tm:<3} atz={atz:<4} "
                          f"amx={amx:<4}  HP m+m={hp_mm:>8,.0f}  "
                          f"portfolio m+m={tot_mm:>10,.0f}",
                          flush=True)
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)

    # ===== Best by HP m+m =====
    print("\n" + "=" * 100)
    print("BEST HP_CFG by HP m+m")
    print("=" * 100)
    best_hp = max(results.items(), key=lambda kv: sum(kv[1][0]) / 4 + min(kv[1][0]))
    (tm, atz, amx), (hp_pd, tot_pd) = best_hp
    hp_mm = sum(hp_pd) / 4 + min(hp_pd)
    print(f"  tm={tm}  atz={atz}  amx={amx}  HP m+m = {hp_mm:,.0f}  "
          f"(vs ref {REF_HP_MM:,} → Δ {hp_mm-REF_HP_MM:+,.0f})")
    print(f"  HP per-day: d0={fmt(hp_pd[0])} d1={fmt(hp_pd[1])} "
          f"d2={fmt(hp_pd[2])} d3={fmt(hp_pd[3])}")

    # ===== Best by portfolio m+m =====
    print("\n" + "=" * 100)
    print("BEST HP_CFG by PORTFOLIO m+m")
    print("=" * 100)
    best_port = max(results.items(), key=lambda kv: sum(kv[1][1]) / 4 + min(kv[1][1]))
    (tm, atz, amx), (hp_pd, tot_pd) = best_port
    tot_mm = sum(tot_pd) / 4 + min(tot_pd)
    print(f"  tm={tm}  atz={atz}  amx={amx}  portfolio m+m = {tot_mm:,.0f}")
    print(f"  portfolio per-day: d0={fmt(tot_pd[0])} d1={fmt(tot_pd[1])} "
          f"d2={fmt(tot_pd[2])} d3={fmt(tot_pd[3])}")

    # ===== Top-5 by portfolio m+m =====
    print("\nTop 5 by portfolio m+m:")
    rows = sorted(
        results.items(),
        key=lambda kv: sum(kv[1][1]) / 4 + min(kv[1][1]),
        reverse=True,
    )[:5]
    for (tm, atz, amx), (hp_pd, tot_pd) in rows:
        hp_mm = sum(hp_pd) / 4 + min(hp_pd)
        tot_mm = sum(tot_pd) / 4 + min(tot_pd)
        print(f"  tm={tm:<3} atz={atz:<4} amx={amx:<4}  HP m+m={hp_mm:>8,.0f}  "
              f"portfolio m+m={tot_mm:>10,.0f}")


if __name__ == "__main__":
    main()
