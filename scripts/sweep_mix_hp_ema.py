"""
Tune HP_CFG EMA + conviction-blend params (ema_fast × ema_slow × w_z)
inside z_take_per_asset_mix.py.

The earlier sweep (sweep_mix_hp_params) covered take_max / aggr_z_thresh
/ aggr_max_take and found current config is near-optimal. This sweep
hits the next-most-impactful knobs: EMA smoothing + conviction blend.

Decision rule (per user instruction): if best HP m+m here drops to
≈z-take's HP ceiling (78,870 from sweep_combined_hp_vev5200), switch
HP to pure z-take in the mix for simplicity.

Usage:  python scripts/sweep_mix_hp_ema.py
"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
STRAT = REPO / "strategies" / "round4" / "z_take_per_asset_mix.py"
DAY_KEYS = [("round3", 0), ("round4", 1), ("round4", 2), ("round4", 3)]

EMA_FAST_GRID = [0.20, 0.30, 0.40]
EMA_SLOW_GRID = [0.03, 0.05, 0.08]
W_Z_GRID = [0.5, 0.625, 0.75]  # w_ema = 1 - w_z

REF_HP_MM = 91_453        # current HP m+m
ZTAKE_HP_CEILING = 78_870  # best HP via pure z-take


def patch(src: str, ef: float, es: float, wz: float) -> str:
    we = round(1.0 - wz, 4)
    src = re.sub(r'("ema_fast"\s*:\s*)[\d.]+', rf'\g<1>{ef}', src)
    src = re.sub(r'("ema_slow"\s*:\s*)[\d.]+', rf'\g<1>{es}', src)
    src = re.sub(r'("w_z"\s*:\s*)[\d.]+', rf'\g<1>{wz}', src)
    src = re.sub(r'("w_ema"\s*:\s*)[\d.]+', rf'\g<1>{we}', src)
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
    backup = STRAT.with_suffix(".py.hpema_bak")
    backup.write_text(original)

    n_runs = len(EMA_FAST_GRID) * len(EMA_SLOW_GRID) * len(W_Z_GRID) * 4
    print(f"Sweeping ema_fast ∈ {EMA_FAST_GRID}")
    print(f"         ema_slow ∈ {EMA_SLOW_GRID}")
    print(f"             w_z  ∈ {W_Z_GRID}")
    print(f"Total: {n_runs} backtests\n")

    results: dict[tuple[float, float, float], tuple[list[float], list[float]]] = {}

    try:
        idx = 0
        total_combos = len(EMA_FAST_GRID) * len(EMA_SLOW_GRID) * len(W_Z_GRID)
        for ef in EMA_FAST_GRID:
            for es in EMA_SLOW_GRID:
                for wz in W_Z_GRID:
                    idx += 1
                    STRAT.write_text(patch(original, ef, es, wz))
                    hp_per_day = [0.0] * 4
                    tot_per_day = [0.0] * 4
                    for d, (ds, day) in enumerate(DAY_KEYS):
                        hp, tot = run_one_day(ds, day)
                        hp_per_day[d] = hp
                        tot_per_day[d] = tot
                    results[(ef, es, wz)] = (hp_per_day, tot_per_day)
                    hp_mm = sum(hp_per_day) / 4 + min(hp_per_day)
                    tot_mm = sum(tot_per_day) / 4 + min(tot_per_day)
                    print(f"[{idx:>2}/{total_combos}] ef={ef:<5} es={es:<5} "
                          f"wz={wz:<6}  HP m+m={hp_mm:>8,.0f}  "
                          f"portfolio m+m={tot_mm:>10,.0f}",
                          flush=True)
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)

    # Best by HP m+m
    print("\n" + "=" * 100)
    print("BEST HP_CFG EMA tune by HP m+m")
    print("=" * 100)
    best_hp = max(results.items(), key=lambda kv: sum(kv[1][0]) / 4 + min(kv[1][0]))
    (ef, es, wz), (hp_pd, tot_pd) = best_hp
    hp_mm = sum(hp_pd) / 4 + min(hp_pd)
    print(f"  ef={ef}  es={es}  wz={wz}  HP m+m = {hp_mm:,.0f}  "
          f"(vs ref {REF_HP_MM:,} → Δ {hp_mm-REF_HP_MM:+,.0f})")

    # Best by portfolio m+m
    print("\n" + "=" * 100)
    print("BEST HP_CFG EMA tune by PORTFOLIO m+m")
    print("=" * 100)
    best_port = max(results.items(), key=lambda kv: sum(kv[1][1]) / 4 + min(kv[1][1]))
    (ef, es, wz), (hp_pd, tot_pd) = best_port
    tot_mm = sum(tot_pd) / 4 + min(tot_pd)
    hp_mm = sum(hp_pd) / 4 + min(hp_pd)
    print(f"  ef={ef}  es={es}  wz={wz}  portfolio m+m = {tot_mm:,.0f}  "
          f"(HP m+m = {hp_mm:,.0f})")
    print(f"  per-day: d0={fmt(tot_pd[0])} d1={fmt(tot_pd[1])} "
          f"d2={fmt(tot_pd[2])} d3={fmt(tot_pd[3])}")

    # Decision: switch to z-take if no_marks HP gain has evaporated
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
