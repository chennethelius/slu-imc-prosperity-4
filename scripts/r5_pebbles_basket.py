#!/usr/bin/env python3
"""Investigate PEBBLES basket structure: XL is anticorrelated with all others.

If basket = a*(XS+S+M+L) + b*XL is stationary, we can mean-revert deviations.
"""
import csv
from collections import defaultdict
from pathlib import Path
import statistics


def load_mids(path):
    out = defaultdict(list)
    with open(path, newline="") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        idx = {c: i for i, c in enumerate(header)}
        for row in reader:
            if not row or len(row) < len(header): continue
            try:
                ts = int(row[idx["timestamp"]])
                prod = row[idx["product"]]
                mid_s = row[idx["mid_price"]]
                if not mid_s: continue
                out[prod].append((ts, float(mid_s)))
            except (ValueError, KeyError):
                continue
    return out


def main():
    repo = Path(__file__).resolve().parent.parent
    data_root = repo / "datasets_extra" / "round5_data"
    days = [2, 3, 4]
    pebbles = ["PEBBLES_XS", "PEBBLES_S", "PEBBLES_M", "PEBBLES_L", "PEBBLES_XL"]

    print("PEBBLES basket: XL vs OTHERS (corr ~ -0.50 across days)\n")

    # Strategy: small_sum = XS+S+M+L; big = XL.
    # Linear: small_sum + 2*big should be stationary if XL has weight -0.5.
    # Check: rolling spread and tradeability.
    print(f"{'day':>4} {'spread basket':>30} {'mean':>10} {'sd':>10} {'max_dev':>10} {'n_above_2sd':>12}")

    for d in days:
        mids = load_mids(data_root / f"prices_round_5_day_{d}.csv")
        seqs = {p: [m for _, m in sorted(mids[p])] for p in pebbles}
        n = min(len(s) for s in seqs.values())
        if n < 100:
            print(f"  day {d}: insufficient data")
            continue
        # Try basket: small_sum + k*XL for various k. Find k that minimizes spread variance.
        # Theoretical: if corr(diff_i, diff_XL) = -0.5 for each i, then
        # cov(small_sum_diffs, XL_diffs) = 4 * (-0.5) * sd_i * sd_XL
        # var(small_sum) = 4 * sd_i^2 (if independent)
        # optimal k for hedge: -cov / var(small_sum) = ...
        # Easier: test k=1, k=2, k=4
        for k in [1, 2, 4]:
            basket = [seqs["PEBBLES_XS"][i] + seqs["PEBBLES_S"][i] + seqs["PEBBLES_M"][i] + seqs["PEBBLES_L"][i] + k * seqs["PEBBLES_XL"][i]
                     for i in range(n)]
            mu = statistics.mean(basket)
            sd = statistics.stdev(basket)
            max_dev = max(abs(b - mu) for b in basket)
            n_above_2sd = sum(1 for b in basket if abs(b - mu) > 2 * sd)
            print(f"  d{d:<2}  small_sum+{k}*XL          {mu:>10.0f} {sd:>10.1f} {max_dev:>10.1f} {n_above_2sd:>12}")

    print("\nPair-wise XL spreads (XL+other for anticorrelated structure):")
    print(f"{'day':>4} {'pair':>30} {'mean':>10} {'sd':>10} {'max_dev':>10} {'n>2sd':>8}")
    for d in days:
        mids = load_mids(data_root / f"prices_round_5_day_{d}.csv")
        seqs = {p: [m for _, m in sorted(mids[p])] for p in pebbles}
        n = min(len(s) for s in seqs.values())
        for other in ["PEBBLES_XS", "PEBBLES_S", "PEBBLES_M", "PEBBLES_L"]:
            spread = [seqs["PEBBLES_XL"][i] + seqs[other][i] for i in range(n)]
            mu = statistics.mean(spread)
            sd = statistics.stdev(spread)
            max_dev = max(abs(s - mu) for s in spread)
            n_above_2sd = sum(1 for s in spread if abs(s - mu) > 2 * sd)
            print(f"  d{d:<2}  XL+{other:<14}      {mu:>10.0f} {sd:>10.1f} {max_dev:>10.1f} {n_above_2sd:>8}")


if __name__ == "__main__":
    main()
