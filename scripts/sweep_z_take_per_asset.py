"""
Per-asset (z_thresh × take_size) sweep for z_take.py.

Key trick: in z_take.py each product is independent — no shared state, no
cross-product positions. So per-product PnL at config (z_X, t_X) is the same
regardless of what other products are doing. We can therefore run the strategy
with a single global (z, t) pair, parse per-product PnL from --products full,
and treat each product's column as if it were its own isolated sweep.

Grid: |Z| × |T| × 4 days backtests total (not |Z| × |T| × N_products × 4).

Scores by per-day mean + per-day min (per CLAUDE.md and team preference —
IMC runs a single day, so we want robustness across days, not 3-day sum).

Usage:  python scripts/sweep_z_take_per_asset.py
"""
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
STRAT = REPO / "strategies" / "round4" / "z_take.py"
DAY_KEYS = [("round3", 0), ("round4", 1), ("round4", 2), ("round4", 3)]
DAY_LABELS = ["d0", "d1", "d2", "d3"]

Z_GRID = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5]
T_GRID = [10, 17, 25, 50, 100]

PRODUCTS = [
    "HYDROGEL_PACK", "VELVETFRUIT_EXTRACT",
    "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100",
    "VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500",
]


def patch(src: str, z: float, t: int) -> str:
    src = re.sub(r'("z_thresh"\s*:\s*)[\d.]+', rf'\g<1>{z}', src)
    src = re.sub(r'("take_size"\s*:\s*)\d+', rf'\g<1>{t}', src)
    return src


def run_one_day(dataset: str, day: int) -> dict[str, float]:
    """Run one (dataset, day) and return {product: pnl}."""
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


def run_4_days() -> dict[str, list[float]]:
    """Return {product: [pnl_d0, pnl_d1, pnl_d2, pnl_d3]}."""
    out: dict[str, list[float]] = {p: [0.0] * 4 for p in PRODUCTS}
    for i, (ds, day) in enumerate(DAY_KEYS):
        per_prod = run_one_day(ds, day)
        for p in PRODUCTS:
            out[p][i] = per_prod.get(p, 0.0)
    return out


def score(per_day: list[float]) -> float:
    return sum(per_day) / len(per_day) + min(per_day)


def fmt_signed(x: float, w: int = 8) -> str:
    return f"{x:>{w},.0f}"


def main() -> None:
    original = STRAT.read_text()
    backup = STRAT.with_suffix(".py.zsabak")
    backup.write_text(original)

    # results[(z, t)][product] = [pnl_d0, pnl_d1, pnl_d2, pnl_d3]
    results: dict[tuple[float, int], dict[str, list[float]]] = {}

    n_runs = len(Z_GRID) * len(T_GRID) * 4
    print(f"Sweeping z_thresh ∈ {Z_GRID}")
    print(f"        take_size ∈ {T_GRID}")
    print(f"Total: {len(Z_GRID)} × {len(T_GRID)} × 4 days = {n_runs} backtests\n")

    try:
        for zi, z in enumerate(Z_GRID):
            for ti, t in enumerate(T_GRID):
                STRAT.write_text(patch(original, z, t))
                idx = zi * len(T_GRID) + ti + 1
                total_combos = len(Z_GRID) * len(T_GRID)
                print(f"[{idx:>2}/{total_combos}] z={z:<5} t={t:<3} ...",
                      end=" ", flush=True)
                per_prod = run_4_days()
                results[(z, t)] = per_prod
                # Sum of all products' total PnL — sanity tag
                total = sum(sum(v) for v in per_prod.values())
                print(f"total 4-day PnL = {total:>11,.0f}", flush=True)
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)

    # ===========================================================
    # Per-asset best (z, t) — score by per-day mean + per-day min
    # ===========================================================
    print("\n" + "=" * 100)
    print("PER-ASSET BEST (z, t) — scored by per-day mean + per-day min")
    print("=" * 100)
    print(f"{'PRODUCT':<22} {'z':>5} {'t':>4}  "
          f"{'d0':>9} {'d1':>9} {'d2':>9} {'d3':>9}  "
          f"{'mean':>9} {'min':>9} {'m+m':>10}  vs baseline (z=1.0,t=17)")
    print("-" * 100)

    baseline_key = (1.0, 17)
    if baseline_key not in results:
        # Should be in grid; if not, skip diff
        print("(baseline z=1.0,t=17 not in grid — diffs omitted)")
        baseline = None
    else:
        baseline = results[baseline_key]

    per_asset_best: dict[str, tuple[float, int]] = {}
    sum_best_score = 0.0
    sum_baseline_score = 0.0

    for p in PRODUCTS:
        best_combo = None
        best_score_val = -1e18
        for (z, t), pp in results.items():
            s = score(pp[p])
            if s > best_score_val:
                best_score_val = s
                best_combo = (z, t)
        z_b, t_b = best_combo
        per_day = results[(z_b, t_b)][p]
        mn = sum(per_day) / 4
        mi = min(per_day)
        per_asset_best[p] = (z_b, t_b)
        sum_best_score += mn + mi

        if baseline is not None:
            base_pd = baseline[p]
            base_mm = sum(base_pd) / 4 + min(base_pd)
            sum_baseline_score += base_mm
            delta = (mn + mi) - base_mm
            tag = f"Δ m+m = {delta:>+10,.0f}"
        else:
            tag = ""

        print(f"{p:<22} {z_b:>5} {t_b:>4}  "
              f"{fmt_signed(per_day[0],9)} {fmt_signed(per_day[1],9)} "
              f"{fmt_signed(per_day[2],9)} {fmt_signed(per_day[3],9)}  "
              f"{fmt_signed(mn,9)} {fmt_signed(mi,9)} {fmt_signed(mn+mi,10)}  {tag}")

    if baseline is not None:
        # Total per-day PnL across all products under best per-asset config
        total_best_per_day = [0.0] * 4
        total_base_per_day = [0.0] * 4
        for p in PRODUCTS:
            zb, tb = per_asset_best[p]
            for d in range(4):
                total_best_per_day[d] += results[(zb, tb)][p][d]
                total_base_per_day[d] += baseline[p][d]
        best_mn = sum(total_best_per_day) / 4
        best_mi = min(total_best_per_day)
        base_mn = sum(total_base_per_day) / 4
        base_mi = min(total_base_per_day)
        print("-" * 100)
        print(f"{'PORTFOLIO best':<22} {'':>5} {'':>4}  "
              f"{fmt_signed(total_best_per_day[0],9)} {fmt_signed(total_best_per_day[1],9)} "
              f"{fmt_signed(total_best_per_day[2],9)} {fmt_signed(total_best_per_day[3],9)}  "
              f"{fmt_signed(best_mn,9)} {fmt_signed(best_mi,9)} {fmt_signed(best_mn+best_mi,10)}")
        print(f"{'PORTFOLIO baseline':<22} {1.0:>5} {17:>4}  "
              f"{fmt_signed(total_base_per_day[0],9)} {fmt_signed(total_base_per_day[1],9)} "
              f"{fmt_signed(total_base_per_day[2],9)} {fmt_signed(total_base_per_day[3],9)}  "
              f"{fmt_signed(base_mn,9)} {fmt_signed(base_mi,9)} {fmt_signed(base_mn+base_mi,10)}")
        print(f"{'PORTFOLIO Δ':<22} {'':>5} {'':>4}  "
              f"{fmt_signed(total_best_per_day[0]-total_base_per_day[0],9)} "
              f"{fmt_signed(total_best_per_day[1]-total_base_per_day[1],9)} "
              f"{fmt_signed(total_best_per_day[2]-total_base_per_day[2],9)} "
              f"{fmt_signed(total_best_per_day[3]-total_base_per_day[3],9)}  "
              f"{fmt_signed(best_mn-base_mn,9)} {fmt_signed(best_mi-base_mi,9)} "
              f"{fmt_signed((best_mn+best_mi)-(base_mn+base_mi),10)}")

    # ===========================================================
    # Suggested CFGS patch
    # ===========================================================
    print("\n" + "=" * 100)
    print("Suggested per-asset config (paste into z_take.py CFGS):")
    print("=" * 100)
    for p in PRODUCTS:
        z, t = per_asset_best[p]
        print(f'  {p}: z_thresh={z}, take_size={t}')


if __name__ == "__main__":
    main()
