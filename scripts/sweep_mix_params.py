"""
Re-sweep (z_thresh, take_size, prior) on the 8 z-take products inside
z_take_per_asset_mix.py. HP and VEV_5200 use no_marks-derived logic and
are NOT touched by this sweep — only the CFGS list (which only contains
the 8 z-take products) is patched.

Per-product PnL is independent across the mix (no shared state between
HP / VEV_5200 / z-take), so we apply ONE global (z, t, prior) at a time
to all 8 CFGS rows, parse per-product PnL from --products full, and
treat each product's column as its own isolated 3D sweep.

Usage:  python scripts/sweep_mix_params.py
"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
STRAT = REPO / "strategies" / "round4" / "tmp" / "z_take_per_asset_mix.py"
DAY_KEYS = [("round3", 0), ("round4", 1), ("round4", 2), ("round4", 3)]

Z_GRID = [0.5, 1.0, 1.25, 1.5, 2.0]
T_GRID = [10, 17, 25, 50]
PRIOR_GRID = [200, 500, 2000, 10000, 50000, 10**9]

# z-take products in mix (HP and VEV_5200 are excluded)
PRODUCTS = [
    "VELVETFRUIT_EXTRACT",
    "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100",
    "VEV_5300", "VEV_5400", "VEV_5500",
]


def patch(src: str, z: float, t: int, prior: int) -> str:
    """Patch every CFGS row simultaneously. Note: prior values >= 10**9
    are written as bare ints (not 10**9 expression) to avoid regex
    surprises with **."""
    src = re.sub(r'("z_thresh"\s*:\s*)[\d.]+', rf'\g<1>{z}', src)
    src = re.sub(r'("take_size"\s*:\s*)\d+', rf'\g<1>{t}', src)
    # CFGS rows have either "prior": 10**9 (literal) or "prior": <int>.
    # Match either form and rewrite to the literal int.
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
    backup = STRAT.with_suffix(".py.mixbak")
    backup.write_text(original)

    n_runs = len(Z_GRID) * len(T_GRID) * len(PRIOR_GRID) * 4
    print(f"Sweeping z ∈ {Z_GRID}")
    print(f"         t ∈ {T_GRID}")
    print(f"     prior ∈ {[('inf' if p>=10**9 else p) for p in PRIOR_GRID]}")
    print(f"Total: {len(Z_GRID)} × {len(T_GRID)} × {len(PRIOR_GRID)} × 4 days = {n_runs} backtests\n")

    # results[(z, t, prior)][product] = [pnl_d0, pnl_d1, pnl_d2, pnl_d3]
    results: dict[tuple[float, int, int], dict[str, list[float]]] = {}

    try:
        idx = 0
        total = len(Z_GRID) * len(T_GRID) * len(PRIOR_GRID)
        for z in Z_GRID:
            for t in T_GRID:
                for prior in PRIOR_GRID:
                    idx += 1
                    STRAT.write_text(patch(original, z, t, prior))
                    per_prod: dict[str, list[float]] = {p: [0.0] * 4 for p in PRODUCTS}
                    for d, (ds, day) in enumerate(DAY_KEYS):
                        got = run_one_day(ds, day)
                        for p in PRODUCTS:
                            per_prod[p][d] = got.get(p, 0.0)
                    results[(z, t, prior)] = per_prod
                    z_take_total = sum(sum(per_prod[p]) for p in PRODUCTS)
                    p_lbl = "inf" if prior >= 10**9 else str(prior)
                    print(f"[{idx:>3}/{total}] z={z:<5} t={t:<3} prior={p_lbl:>7}  "
                          f"z-take 4-day total = {z_take_total:>11,.0f}",
                          flush=True)
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)

    # Per-product best (z, t, prior)
    print("\n" + "=" * 100)
    print("PER-PRODUCT BEST (z, t, prior) — scored by per-day mean + per-day min")
    print("=" * 100)
    print(f"{'PRODUCT':<22} {'z':>5} {'t':>4} {'prior':>9}  "
          f"{'d0':>9} {'d1':>9} {'d2':>9} {'d3':>9}  "
          f"{'mean':>9} {'min':>9} {'m+m':>10}")
    print("-" * 100)
    per_asset_best: dict[str, tuple[float, int, int]] = {}
    for p in PRODUCTS:
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
        per_asset_best[p] = (z, t, prior)
        p_lbl = "inf" if prior >= 10**9 else str(prior)
        print(f"{p:<22} {z:>5} {t:>4} {p_lbl:>9}  "
              f"{fmt(pd[0])} {fmt(pd[1])} {fmt(pd[2])} {fmt(pd[3])}  "
              f"{fmt(mn)} {fmt(mi)} {fmt(mn+mi,10)}")

    # Implied portfolio under per-asset best — for context, sum across
    # z-take products only (HP and VEV_5200 are unaffected).
    print("\nSuggested updated CFGS for z_take_per_asset_mix.py:")
    for p in PRODUCTS:
        z, t, prior = per_asset_best[p]
        p_lbl = "10**9" if prior >= 10**9 else str(prior)
        print(f"  {p}: z_thresh={z}, take_size={t}, prior={p_lbl}")


if __name__ == "__main__":
    main()
