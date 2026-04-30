#!/usr/bin/env python3
"""Search ALL 1225 product pairs for persistent cointegration.

For each pair across all 50 products (not just within-group), compute mid-
return correlation across days 2/3/4. Report pairs where:
  - |corr| > 0.7 on every day (strong, persistent relationship)
  - corr direction is consistent (all positive or all negative)

These are candidates for v22-style pair mean-reversion alpha.
"""
import csv
from collections import defaultdict
from pathlib import Path


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


def correlate(seq_a, seq_b):
    if len(seq_a) != len(seq_b) or len(seq_a) < 3:
        return None
    mu_a = sum(seq_a) / len(seq_a)
    mu_b = sum(seq_b) / len(seq_b)
    num = sum((a - mu_a) * (b - mu_b) for a, b in zip(seq_a, seq_b))
    den_a = sum((a - mu_a) ** 2 for a in seq_a) ** 0.5
    den_b = sum((b - mu_b) ** 2 for b in seq_b) ** 0.5
    if den_a == 0 or den_b == 0:
        return None
    return num / (den_a * den_b)


def diffs(seq):
    return [seq[i] - seq[i - 1] for i in range(1, len(seq))]


def main():
    repo = Path(__file__).resolve().parent.parent
    data_root = repo / "datasets_extra" / "round5_data"
    days = [2, 3, 4]

    # Load all per-day mids
    all_day_mids = {}
    for d in days:
        mids = load_mids(data_root / f"prices_round_5_day_{d}.csv")
        all_day_mids[d] = {p: [m for _, m in sorted(mids[p])] for p in mids}

    products = sorted(all_day_mids[days[0]].keys())
    print(f"Found {len(products)} products. Computing all-pairs correlations across 3 days...")

    strong_pairs = []
    for i, a in enumerate(products):
        for b in products[i+1:]:
            day_corrs = []
            for d in days:
                seq_a = all_day_mids[d].get(a, [])
                seq_b = all_day_mids[d].get(b, [])
                n = min(len(seq_a), len(seq_b))
                if n < 100:
                    day_corrs.append(None)
                    continue
                da = diffs(seq_a[:n])
                db = diffs(seq_b[:n])
                day_corrs.append(correlate(da, db))
            valid = [c for c in day_corrs if c is not None]
            if len(valid) < 3:
                continue
            min_abs = min(abs(c) for c in valid)
            # Persistent only if all same sign
            signs = set(1 if c > 0 else -1 for c in valid)
            if len(signs) > 1:
                continue
            if min_abs > 0.7:
                strong_pairs.append((min_abs, signs.pop(), a, b, day_corrs))

    strong_pairs.sort(key=lambda x: -x[0])
    print(f"\nFound {len(strong_pairs)} strongly persistent pairs (|min_corr| > 0.7):\n")
    print(f"{'corr_sign':>9} {'min_abs':>8} {'product_a':<35} {'product_b':<35} {'d2':>6} {'d3':>6} {'d4':>6}")
    for min_abs, sign, a, b, corrs in strong_pairs[:50]:
        c_strs = " ".join(f"{c:>5.2f}" if c is not None else "  n/a " for c in corrs)
        sign_lbl = "+" if sign > 0 else "-"
        print(f"{sign_lbl:>9} {min_abs:>8.3f} {a:<35} {b:<35} {c_strs}")


if __name__ == "__main__":
    main()
