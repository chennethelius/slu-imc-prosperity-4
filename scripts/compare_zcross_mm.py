"""
A/B test: plain z-take vs zcross+MM (3-layer) for VEV_4000 / VEV_4500.

Wide bid-ask spreads on these products suggest market-making could
capture the spread on top of the existing z-take signal. This script
toggles ZCROSS_MM_CFGS in z_take_per_asset_mix.py to enable the 3-layer
strategy for both products, then reports per-product and portfolio PnL.

Layers (zcross_mm):
  1. z-take when |z| >= z_thresh (same as plain z-take)
  2. zero-cross harvest — flatten existing position when z changes sign
  3. MM passive quotes inside the bid/ask spread

Usage:  python scripts/compare_zcross_mm.py [qsize]
"""
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
STRAT = REPO / "strategies" / "round4" / "z_take_per_asset_mix.py"
DAY_KEYS = [("round3", 0), ("round4", 1), ("round4", 2), ("round4", 3)]

# Start with same z_thresh / take_size / prior as the current z-take CFGS
ZCROSS_TARGETS = [
    {"symbol": "VEV_4000", "mean": 1247, "sd": 17.114,
     "z_thresh": 1.5, "take_size": 10, "limit": 300, "prior": 10**9},
    {"symbol": "VEV_4500", "mean":  747, "sd": 17.105,
     "z_thresh": 1.5, "take_size": 10, "limit": 300, "prior": 10**9},
]


def patch_cfgs(src: str, cfgs: list[dict]) -> str:
    """Replace ZCROSS_MM_CFGS = [...] line with a fresh literal."""
    if not cfgs:
        rendered = "[]"
    else:
        items = []
        for c in cfgs:
            items.append(
                '{"symbol": "%s", "mean": %d, "sd": %s, "z_thresh": %s, '
                '"take_size": %d, "limit": %d, "prior": %s, "qsize": %d}'
                % (c["symbol"], c["mean"], c["sd"], c["z_thresh"],
                   c["take_size"], c["limit"], c["prior"], c["qsize"])
            )
        rendered = "[" + ", ".join(items) + "]"
    return re.sub(
        r'^ZCROSS_MM_CFGS\s*:\s*list\s*=.*$',
        f'ZCROSS_MM_CFGS: list = {rendered}',
        src, count=1, flags=re.MULTILINE,
    )


def run_one_day(dataset: str, day: int) -> tuple[dict[str, float], float]:
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


def measure(label: str) -> tuple[dict[str, list[float]], list[float]]:
    """Run 4 days, returning ({product: per_day [4]}, total_per_day [4])."""
    per_prod: dict[str, list[float]] = {}
    tot_pd = [0.0] * 4
    for d, (ds, day) in enumerate(DAY_KEYS):
        per, tot = run_one_day(ds, day)
        for p, v in per.items():
            per_prod.setdefault(p, [0.0] * 4)[d] = v
        tot_pd[d] = tot
    print(f"{label}  d0={fmt(tot_pd[0])} d1={fmt(tot_pd[1])} "
          f"d2={fmt(tot_pd[2])} d3={fmt(tot_pd[3])}  "
          f"mean={fmt(sum(tot_pd)/4)} min={fmt(min(tot_pd))} "
          f"m+m={fmt(sum(tot_pd)/4 + min(tot_pd),10)}",
          flush=True)
    return per_prod, tot_pd


def main() -> None:
    qsize = int(sys.argv[1]) if len(sys.argv) > 1 else 30

    original = STRAT.read_text()
    backup = STRAT.with_suffix(".py.zcrossbak")
    backup.write_text(original)

    targets = [{**c, "qsize": qsize} for c in ZCROSS_TARGETS]

    try:
        # Baseline: ZCROSS_MM_CFGS = [] (current behavior, plain z-take)
        STRAT.write_text(patch_cfgs(original, []))
        base_pp, base_tot = measure("baseline (plain z-take)         ")

        # Treatment: ZCROSS_MM_CFGS contains VEV_4000 and VEV_4500
        STRAT.write_text(patch_cfgs(original, targets))
        zc_pp, zc_tot = measure(f"zcross+MM qsize={qsize:<3} on 4000/4500")
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)

    # Per-product comparison
    print("\n" + "=" * 100)
    print(f"PER-PRODUCT m+m (qsize={qsize})")
    print("=" * 100)
    print(f"{'PRODUCT':<22}  {'baseline m+m':>13}  {'zcross+MM m+m':>14}  {'Δ':>10}")
    print("-" * 100)
    products = sorted(set(base_pp) | set(zc_pp))
    for p in products:
        b_pd = base_pp.get(p, [0.0] * 4)
        z_pd = zc_pp.get(p, [0.0] * 4)
        if not any(b_pd) and not any(z_pd):
            continue
        b_mm = sum(b_pd) / 4 + min(b_pd)
        z_mm = sum(z_pd) / 4 + min(z_pd)
        marker = " ★" if p in ("VEV_4000", "VEV_4500") else "  "
        print(f"{p:<22}{marker}{fmt(b_mm,13)}  {fmt(z_mm,14)}  {fmt(z_mm-b_mm,10)}")

    # Portfolio summary
    b_mm = sum(base_tot) / 4 + min(base_tot)
    z_mm = sum(zc_tot) / 4 + min(zc_tot)
    print()
    print(f"PORTFOLIO m+m  baseline={b_mm:,.0f}  zcross+MM={z_mm:,.0f}  Δ={z_mm-b_mm:+,.0f}")


if __name__ == "__main__":
    main()
