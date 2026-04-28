"""
Tune (z_thresh, take_size, prior) for HP and VEV_5200 inside
z_take_combined.py with a denser grid than the original per-asset sweep.

Goal: see if pure z-take with finer-tuned params can match the no_marks
logic that hybrid uses for these two products. If yes → mix can drop the
no_marks layer and revert to pure combined. If no → the no_marks layer
stays.

The strategy applies one global (z, t, prior) at a time to all 10 CFGS
rows; per-product PnL is independent so we still get correct HP and
VEV_5200 numbers without needing per-product patching.

Usage:  python scripts/sweep_combined_hp_vev5200.py
"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
STRAT = REPO / "strategies" / "round4" / "tmp" / "z_take_combined.py"
DAY_KEYS = [("round3", 0), ("round4", 1), ("round4", 2), ("round4", 3)]

# Denser around the previous best (HP@z=1.0,t=10,static; VEV5200@z=0.5,t=10,p=2000)
Z_GRID = [0.25, 0.5, 0.75, 1.0, 1.25]
T_GRID = [5, 10, 17, 25]
PRIOR_GRID = [200, 500, 2000, 10000, 50000, 10**9]

TARGET_PRODUCTS = ("HYDROGEL_PACK", "VEV_5200")

# Reference (no_marks-style) PnL on these products from compare_z_take_variants:
#   HP via hybrid (no_marks):     m+m = 91,453
#   VEV_5200 via raw no_marks:    m+m = 38,530
REF = {"HYDROGEL_PACK": 91_453, "VEV_5200": 38_530}


def patch(src: str, z: float, t: int, prior: int) -> str:
    src = re.sub(r'("z_thresh"\s*:\s*)[\d.]+', rf'\g<1>{z}', src)
    src = re.sub(r'("take_size"\s*:\s*)\d+', rf'\g<1>{t}', src)
    src = re.sub(r'("prior"\s*:\s*)(?:\d+\*\*\d+|\d+)', rf'\g<1>{prior}', src)
    return src


def run_one_day(dataset: str, day: int) -> dict[str, float]:
    r = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--",
         "--trader", str(STRAT), "--dataset", dataset, f"--day={day}",
         "--queue-penetration", "1.0", "--products", "full",
         "--artifact-mode", "none"],
        capture_output=True, text=True, timeout=240, cwd=str(BT_DIR),
    )
    out: dict[str, float] = {}
    in_table = False
    for line in r.stdout.splitlines():
        if line.startswith("PRODUCT"):
            in_table = True
            continue
        if in_table:
            if not line.strip():
                break
            parts = line.split()
            if len(parts) >= 2:
                try:
                    out[parts[0]] = float(parts[1])
                except ValueError:
                    pass
    return out


def fmt(x: float, w: int = 9) -> str:
    return f"{x:>{w},.0f}"


def main() -> None:
    original = STRAT.read_text()
    backup = STRAT.with_suffix(".py.hpvev_bak")
    backup.write_text(original)

    n_runs = len(Z_GRID) * len(T_GRID) * len(PRIOR_GRID) * 4
    print(f"Sweeping z ∈ {Z_GRID}")
    print(f"         t ∈ {T_GRID}")
    print(f"     prior ∈ {[('inf' if p>=10**9 else p) for p in PRIOR_GRID]}")
    print(f"Targeting: {TARGET_PRODUCTS}")
    print(f"Total: {len(Z_GRID)} × {len(T_GRID)} × {len(PRIOR_GRID)} × 4 days "
          f"= {n_runs} backtests\n")

    # results[(z,t,prior)][product] = [pnl_d0, pnl_d1, pnl_d2, pnl_d3]
    results: dict[tuple[float, int, int], dict[str, list[float]]] = {}

    try:
        idx = 0
        total = len(Z_GRID) * len(T_GRID) * len(PRIOR_GRID)
        for z in Z_GRID:
            for t in T_GRID:
                for prior in PRIOR_GRID:
                    idx += 1
                    STRAT.write_text(patch(original, z, t, prior))
                    per_prod: dict[str, list[float]] = {p: [0.0] * 4 for p in TARGET_PRODUCTS}
                    for d, (ds, day) in enumerate(DAY_KEYS):
                        got = run_one_day(ds, day)
                        for p in TARGET_PRODUCTS:
                            per_prod[p][d] = got.get(p, 0.0)
                    results[(z, t, prior)] = per_prod
                    p_lbl = "inf" if prior >= 10**9 else str(prior)
                    hp_mm = sum(per_prod["HYDROGEL_PACK"]) / 4 + min(per_prod["HYDROGEL_PACK"])
                    v_mm = sum(per_prod["VEV_5200"]) / 4 + min(per_prod["VEV_5200"])
                    print(f"[{idx:>3}/{total}] z={z:<5} t={t:<3} prior={p_lbl:>7}  "
                          f"HP m+m={hp_mm:>8,.0f}  VEV_5200 m+m={v_mm:>7,.0f}",
                          flush=True)
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)

    # Per-product best
    print("\n" + "=" * 100)
    print("PER-PRODUCT BEST (z, t, prior) on z-take logic")
    print("=" * 100)
    print(f"{'PRODUCT':<22} {'z':>5} {'t':>4} {'prior':>9}  "
          f"{'d0':>9} {'d1':>9} {'d2':>9} {'d3':>9}  "
          f"{'mean':>9} {'min':>9} {'m+m':>10}  vs no_marks")
    print("-" * 100)
    for p in TARGET_PRODUCTS:
        best = None
        best_score = -1e18
        for key, pp in results.items():
            pd = pp[p]
            score = sum(pd) / 4 + min(pd)
            if score > best_score:
                best_score = score
                best = (key, pd)
        (z, t, prior), pd = best
        mn, mi = sum(pd) / 4, min(pd)
        p_lbl = "inf" if prior >= 10**9 else str(prior)
        ref = REF[p]
        gap = mn + mi - ref
        verdict = "BEATS" if gap >= 0 else "LOSES"
        print(f"{p:<22} {z:>5} {t:>4} {p_lbl:>9}  "
              f"{fmt(pd[0])} {fmt(pd[1])} {fmt(pd[2])} {fmt(pd[3])}  "
              f"{fmt(mn)} {fmt(mi)} {fmt(mn+mi,10)}  "
              f"{verdict} ({gap:+,.0f})")


if __name__ == "__main__":
    main()
