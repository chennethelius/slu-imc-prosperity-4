"""
Walk-forward validation for zscore_mm.py.

For each fold (train_days → test_day):
  1. Compute the 95th-percentile divergence threshold per product from the
     training days only.
  2. Patch zscore_mm.py's PRODUCTS list with those thresholds.
  3. Run the backtester on the test day.
  4. Record per-product PnL.

If the strategy's thresholds were genuinely data-anchored (not curve-fit to
the test set), out-of-sample PnL on the test day should track in-sample PnL.
"""

import csv
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "backtester" / "datasets" / "round3"
TRADER = REPO / "strategies" / "round3" / "zscore_mm.py"


def load_mids(days):
    """Returns {product: ndarray of mids} for the given list of days."""
    out = {}
    for day in days:
        with (DATA / f"prices_round_3_day_{day}.csv").open() as f:
            for r in csv.DictReader(f, delimiter=";"):
                if not r["mid_price"]:
                    continue
                out.setdefault(r["product"], []).append(float(r["mid_price"]))
    return {p: np.array(mids) for p, mids in out.items()}


def compute_thresholds(train_days):
    """For each product, compute round(95th-percentile of |mid − expanding_mean|)."""
    mids_by_product = load_mids(train_days)
    thresholds = {}
    for product, mids in mids_by_product.items():
        if len(mids) < 1500 or mids.std() < 0.5:
            thresholds[product] = 0   # disable
            continue
        anchor = np.cumsum(mids) / np.arange(1, len(mids) + 1)
        diverge = mids - anchor
        warm = diverge[1000:]
        q = max(abs(np.quantile(warm, 0.05)), abs(np.quantile(warm, 0.95)))
        thresholds[product] = max(1, round(q))
    return thresholds


def patch_thresholds(thresholds):
    """Rewrite each product's `diverge_threshold` value. Spans multi-line dicts."""
    text = TRADER.read_text()
    lines = text.splitlines()
    current = None
    for i, line in enumerate(lines):
        m = re.search(r'"product":\s*"([^"]+)"', line)
        if m:
            current = m.group(1)
        if current in thresholds and re.search(r'"diverge_threshold":\s*\d+', line):
            lines[i] = re.sub(
                r'"diverge_threshold":\s*\d+',
                f'"diverge_threshold": {thresholds[current]}',
                line,
            )
            current = None  # only patch first match per product
    TRADER.write_text("\n".join(lines) + "\n")


def run_day(day):
    """Run backtester on a single day, return total PnL."""
    result = subprocess.run(
        ["make", "round3", f"TRADER=../{TRADER.relative_to(REPO)}", f"DAY={day}"],
        cwd=str(REPO), capture_output=True, text=True, timeout=120,
    )
    for line in result.stdout.splitlines():
        if line.startswith(f"D+{day}") or line.startswith(f"D={day}"):
            parts = line.split()
            try:
                return float(parts[-2])
            except (ValueError, IndexError):
                pass
    return None


def main():
    backup = TRADER.with_suffix(".py.walkfwd_backup")
    shutil.copy2(TRADER, backup)
    try:
        # 3-fold: train on 2 days, test on the 3rd.
        folds = [
            ([0, 1], 2),
            ([0, 2], 1),
            ([1, 2], 0),
        ]
        # Plus the full-data run as the in-sample reference.
        in_sample = compute_thresholds([0, 1, 2])
        print("In-sample thresholds (using all 3 days):")
        for product, t in sorted(in_sample.items()):
            print(f"  {product:<22} {t}")
        print()

        print(f"{'fold':<28}{'D=0':>10}{'D+1':>10}{'D+2':>10}{'total':>11}")
        print("-" * 70)

        # Reference: in-sample on each day, summed
        patch_thresholds(in_sample)
        ref_per_day = {d: run_day(d) for d in (0, 1, 2)}
        ref_total = sum(v or 0 for v in ref_per_day.values())
        print(f"{'in-sample (train=all)':<28}"
              f"{ref_per_day[0]:>10.0f}{ref_per_day[1]:>10.0f}{ref_per_day[2]:>10.0f}"
              f"{ref_total:>11.0f}")

        # Walk-forward folds
        oos_pnls = {}
        for train, test in folds:
            thresholds = compute_thresholds(train)
            patch_thresholds(thresholds)
            pnl = run_day(test)
            oos_pnls[test] = pnl
            train_str = "+".join(f"D{d}" for d in train)
            print(f"\n{train_str} → D{test}  thresholds:"
                  + ", ".join(f"{p}={t}" for p, t in sorted(thresholds.items()) if t > 0))
            print(f"  test PnL on D{test} = {pnl:.0f}")

        oos_total = sum(oos_pnls.values())
        print()
        print(f"OUT-OF-SAMPLE PnL summary (each day tested with thresholds from the OTHER two days):")
        for d, pnl in sorted(oos_pnls.items()):
            in_s = ref_per_day[d]
            shrink_pct = 100 * (pnl - in_s) / in_s if in_s else 0
            print(f"  D{d}: in-sample={in_s:>8.0f}   OOS={pnl:>8.0f}   "
                  f"OOS/IS = {pnl/in_s:.2%}  ({shrink_pct:+.1f}%)")
        print(f"  TOTAL OOS = {oos_total:.0f}   vs in-sample {ref_total:.0f}   "
              f"({100*(oos_total-ref_total)/ref_total:+.1f}%)")
    finally:
        TRADER.write_text(backup.read_text())
        backup.unlink()


if __name__ == "__main__":
    main()
