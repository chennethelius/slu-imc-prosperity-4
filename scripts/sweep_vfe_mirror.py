"""
Sweep VFE-mirror logic for VEV_5400 and VEV_5500.

Tests whether trading these vouchers in the same direction as VFE's
z-take signal (only when |VFE_z| >= mirror_z_thresh) beats the standalone
z-take config.

Each iteration patches VFE_DERIVATIVES inside z_take_per_asset_mix.py to
contain ONE target with one mirror_z_thresh, runs 4 days, parses the
target's per-day PnL and the portfolio total. Compares:
  - Target product m+m (vs z-take baseline)
  - Portfolio m+m (vs no-mirror baseline)

Decision rule: keep VFE-mirror only if it improves PORTFOLIO m+m. The
earlier failed attempt for VEV_5500 at thresh=1.0 lost $1,645 portfolio
m+m even though the product gained $235.

Usage:  python scripts/sweep_vfe_mirror.py
"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
STRAT = REPO / "strategies" / "round4" / "z_take_per_asset_mix.py"
DAY_KEYS = [("round3", 0), ("round4", 1), ("round4", 2), ("round4", 3)]

TARGETS = ["VEV_5400", "VEV_5500"]
THRESHOLDS = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5]

# Baselines from current z-take config (no mirror):
BASELINE_TARGET_MM = {"VEV_5400": 12_885, "VEV_5500": 1_364}
BASELINE_PORTFOLIO_MM = 546_608


def patch_derivatives(src: str, derivatives: list[dict]) -> str:
    """Replace `VFE_DERIVATIVES: list = [...]` line with a fresh literal."""
    rendered = "[" + ", ".join(
        '{"symbol": "%s", "limit": %d, "take_size": %d, "mirror_z_thresh": %s}'
        % (d["symbol"], d["limit"], d["take_size"], d["mirror_z_thresh"])
        for d in derivatives
    ) + "]"
    return re.sub(
        r'^VFE_DERIVATIVES\s*:\s*list\s*=.*$',
        f'VFE_DERIVATIVES: list = {rendered}',
        src, count=1, flags=re.MULTILINE,
    )


def run_one_day(dataset: str, day: int) -> tuple[dict[str, float], float]:
    """Returns ({product: pnl}, total)."""
    r = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--",
         "--trader", str(STRAT), "--dataset", dataset, f"--day={day}",
         "--queue-penetration", "1.0", "--products", "full",
         "--artifact-mode", "none"],
        capture_output=True, text=True, timeout=240, cwd=str(BT_DIR),
    )
    per: dict[str, float] = {}
    total = 0.0
    in_table = False
    for line in r.stdout.splitlines():
        if line.startswith("D") and "TICKS" not in line:
            parts = line.split()
            if len(parts) >= 5:
                try: total = float(parts[4])
                except ValueError: pass
        if line.startswith("PRODUCT"):
            in_table = True; continue
        if in_table:
            if not line.strip(): break
            parts = line.split()
            if len(parts) >= 2:
                try: per[parts[0]] = float(parts[1])
                except ValueError: pass
    return per, total


def fmt(x: float, w: int = 9) -> str:
    return f"{x:>{w},.0f}"


def main() -> None:
    original = STRAT.read_text()
    backup = STRAT.with_suffix(".py.vfemirror_bak")
    backup.write_text(original)

    n_runs = len(TARGETS) * len(THRESHOLDS) * 4
    print(f"Targets   : {TARGETS}")
    print(f"Thresholds: {THRESHOLDS}")
    print(f"Total: {n_runs} backtests\n")

    # results[(target, thresh)] = (target_per_day [4], portfolio_per_day [4])
    results: dict[tuple[str, float], tuple[list[float], list[float]]] = {}

    try:
        idx = 0
        total_combos = len(TARGETS) * len(THRESHOLDS)
        for target in TARGETS:
            for thresh in THRESHOLDS:
                idx += 1
                derivatives = [{
                    "symbol": target, "limit": 300,
                    "take_size": 25, "mirror_z_thresh": thresh,
                }]
                STRAT.write_text(patch_derivatives(original, derivatives))
                tgt_pd = [0.0] * 4
                tot_pd = [0.0] * 4
                for d, (ds, day) in enumerate(DAY_KEYS):
                    per, tot = run_one_day(ds, day)
                    tgt_pd[d] = per.get(target, 0.0)
                    tot_pd[d] = tot
                results[(target, thresh)] = (tgt_pd, tot_pd)
                tgt_mm = sum(tgt_pd) / 4 + min(tgt_pd)
                tot_mm = sum(tot_pd) / 4 + min(tot_pd)
                print(f"[{idx:>2}/{total_combos}] {target} thresh={thresh:<5}  "
                      f"target m+m={tgt_mm:>+8,.0f}  portfolio m+m={tot_mm:>10,.0f}",
                      flush=True)
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)

    # Per-target summary
    print()
    for target in TARGETS:
        base_tgt = BASELINE_TARGET_MM[target]
        print("=" * 100)
        print(f"{target}: VFE-mirror sweep results (baseline target m+m = {base_tgt:,}, "
              f"portfolio = {BASELINE_PORTFOLIO_MM:,})")
        print("=" * 100)
        print(f"{'thresh':>7}  {'target m+m':>12}  {'Δ vs base':>10}  "
              f"{'portfolio m+m':>15}  {'Δ portfolio':>12}")
        print("-" * 100)
        rows = []
        for thresh in THRESHOLDS:
            tgt_pd, tot_pd = results[(target, thresh)]
            tgt_mm = sum(tgt_pd) / 4 + min(tgt_pd)
            tot_mm = sum(tot_pd) / 4 + min(tot_pd)
            rows.append((thresh, tgt_mm, tot_mm))
            print(f"{thresh:>7}  {tgt_mm:>+12,.0f}  {tgt_mm - base_tgt:>+10,.0f}  "
                  f"{tot_mm:>+15,.0f}  {tot_mm - BASELINE_PORTFOLIO_MM:>+12,.0f}")
        # Best by portfolio
        best = max(rows, key=lambda r: r[2])
        delta = best[2] - BASELINE_PORTFOLIO_MM
        if delta > 0:
            print(f"\n  best portfolio: thresh={best[0]}  Δ portfolio = {delta:+,.0f}  "
                  f"→ KEEP if MC confirms")
        else:
            print(f"\n  best portfolio: thresh={best[0]}  Δ portfolio = {delta:+,.0f}  "
                  f"→ MIRROR HURTS, keep z-take")


if __name__ == "__main__":
    main()
