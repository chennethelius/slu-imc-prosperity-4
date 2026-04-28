"""
Tune HP_CFG z_min × z_max — entry-signal magnitude bounds — inside
z_take_per_asset_mix.py.

Earlier sweeps:
  sweep_mix_hp_params  : take_max / aggr_z_thresh / aggr_max_take
                         → current is near-optimal
  sweep_mix_hp_ema     : ema_fast / ema_slow / w_z
                         → +$290 m+m at ef=0.20, es=0.08

This sweep: z_min (current 0.7) × z_max (current 2.0). These bound the
z-strength factor used in conviction.

Decision rule (per user instruction): if best HP m+m here drops to
≈z-take's HP ceiling (78,870), switch HP to pure z-take in the mix.

Usage:  python scripts/sweep_mix_hp_zbounds.py
"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
STRAT = REPO / "strategies" / "round4" / "z_take_per_asset_mix.py"
DAY_KEYS = [("round3", 0), ("round4", 1), ("round4", 2), ("round4", 3)]

Z_MIN_GRID = [0.5, 0.6, 0.7, 0.8, 0.9]
Z_MAX_GRID = [1.5, 1.8, 2.0, 2.3, 2.5]

REF_HP_MM = 91_744           # current HP m+m after EMA tune
ZTAKE_HP_CEILING = 78_870    # best HP via pure z-take


def patch(src: str, zmin: float, zmax: float) -> str:
    src = re.sub(r'("z_min"\s*:\s*)[\d.]+', rf'\g<1>{zmin}', src)
    src = re.sub(r'("z_max"\s*:\s*)[\d.]+', rf'\g<1>{zmax}', src)
    return src


def run_one_day(dataset: str, day: int) -> tuple[float, float]:
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
    backup = STRAT.with_suffix(".py.hpzbnd_bak")
    backup.write_text(original)

    n_runs = len(Z_MIN_GRID) * len(Z_MAX_GRID) * 4
    print(f"Sweeping z_min ∈ {Z_MIN_GRID}")
    print(f"         z_max ∈ {Z_MAX_GRID}")
    print(f"Total: {n_runs} backtests\n")

    results: dict[tuple[float, float], tuple[list[float], list[float]]] = {}

    try:
        idx = 0
        total_combos = len(Z_MIN_GRID) * len(Z_MAX_GRID)
        for zmin in Z_MIN_GRID:
            for zmax in Z_MAX_GRID:
                if zmax <= zmin:
                    continue
                idx += 1
                STRAT.write_text(patch(original, zmin, zmax))
                hp_per_day = [0.0] * 4
                tot_per_day = [0.0] * 4
                for d, (ds, day) in enumerate(DAY_KEYS):
                    hp, tot = run_one_day(ds, day)
                    hp_per_day[d] = hp
                    tot_per_day[d] = tot
                results[(zmin, zmax)] = (hp_per_day, tot_per_day)
                hp_mm = sum(hp_per_day) / 4 + min(hp_per_day)
                tot_mm = sum(tot_per_day) / 4 + min(tot_per_day)
                print(f"[{idx:>2}/{total_combos}] zmin={zmin:<4} zmax={zmax:<4}  "
                      f"HP m+m={hp_mm:>8,.0f}  portfolio m+m={tot_mm:>10,.0f}",
                      flush=True)
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)

    # Best by HP m+m
    print("\n" + "=" * 100)
    print("BEST z_min × z_max by HP m+m")
    print("=" * 100)
    best_hp = max(results.items(), key=lambda kv: sum(kv[1][0]) / 4 + min(kv[1][0]))
    (zmin, zmax), (hp_pd, tot_pd) = best_hp
    hp_mm = sum(hp_pd) / 4 + min(hp_pd)
    print(f"  zmin={zmin}  zmax={zmax}  HP m+m = {hp_mm:,.0f}  "
          f"(vs ref {REF_HP_MM:,} → Δ {hp_mm-REF_HP_MM:+,.0f})")

    # Best by portfolio m+m
    print("\n" + "=" * 100)
    print("BEST z_min × z_max by PORTFOLIO m+m")
    print("=" * 100)
    best_port = max(results.items(), key=lambda kv: sum(kv[1][1]) / 4 + min(kv[1][1]))
    (zmin, zmax), (hp_pd, tot_pd) = best_port
    tot_mm = sum(tot_pd) / 4 + min(tot_pd)
    hp_mm = sum(hp_pd) / 4 + min(hp_pd)
    print(f"  zmin={zmin}  zmax={zmax}  portfolio m+m = {tot_mm:,.0f}  "
          f"(HP m+m = {hp_mm:,.0f})")
    print(f"  per-day: d0={fmt(tot_pd[0])} d1={fmt(tot_pd[1])} "
          f"d2={fmt(tot_pd[2])} d3={fmt(tot_pd[3])}")

    # Decision
    print("\n" + "=" * 100)
    print("DECISION: keep no_marks HP or switch to z-take?")
    print("=" * 100)
    best_hp_mm = max(sum(v[0]) / 4 + min(v[0]) for v in results.values())
    edge_over_ztake = best_hp_mm - ZTAKE_HP_CEILING
    print(f"  Best no_marks HP m+m (after tune) : {best_hp_mm:,.0f}")
    print(f"  Z-take HP ceiling                 : {ZTAKE_HP_CEILING:,.0f}")
    print(f"  Edge over z-take                  : {edge_over_ztake:+,.0f}")
    if edge_over_ztake < 1000:
        print("  → no_marks edge ≤ $1k. Switch HP to z-take for code simplicity.")
    else:
        print("  → no_marks still beats z-take by >$1k. Keep no_marks logic.")


if __name__ == "__main__":
    main()
