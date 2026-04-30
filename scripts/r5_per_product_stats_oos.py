#!/usr/bin/env python3
"""Compute PRODUCT_STATS using days 2,3 ONLY (out-of-sample for day 4).

Used to validate v37's reversal-on-baked-stats strategy isn't overfit.
"""
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def load_mids(path):
    out = defaultdict(list)
    with open(path, newline="") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        idx = {c: i for i, c in enumerate(header)}
        for row in reader:
            if not row or len(row) < len(header):
                continue
            try:
                prod = row[idx["product"]]
                mid_s = row[idx["mid_price"]]
                if not mid_s:
                    continue
                out[prod].append(float(mid_s))
            except (ValueError, KeyError):
                continue
    return out


def main():
    repo = Path(__file__).resolve().parent.parent
    data_root = repo / "datasets_extra" / "round5_data"
    train_days = [int(d) for d in sys.argv[1].split(",")] if len(sys.argv) > 1 else [2, 3]

    all_mids = defaultdict(list)
    for d in train_days:
        mids = load_mids(data_root / f"prices_round_5_day_{d}.csv")
        for prod, vals in mids.items():
            all_mids[prod].extend(vals)

    print(f"# Stats computed from days {train_days}")
    print("PRODUCT_STATS = {")
    for prod in sorted(all_mids):
        vals = all_mids[prod]
        if not vals:
            continue
        mu = statistics.mean(vals)
        sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
        print(f"    \"{prod}\": {{\"mean\": {mu:.1f}, \"std\": {sd:.1f}}},")
    print("}")


if __name__ == "__main__":
    main()
