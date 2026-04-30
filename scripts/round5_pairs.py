"""Examine pair relationships in detail for Pebbles and Snackpacks groups."""
import csv, math, statistics
from collections import defaultdict
from pathlib import Path

DATA = Path("C:/Users/thisi/OneDrive/Desktop/IMC Prosperity 4/data/ROUND_5")


def load_mid(day):
    out = defaultdict(dict)
    fn = DATA / f"prices_round_5_day_{day}.csv"
    with open(fn, encoding="utf-8") as f:
        r = csv.DictReader(f, delimiter=";")
        for row in r:
            try:
                ts = int(row["timestamp"])
                p = row["product"]
                m = float(row["mid_price"])
                out[p][ts] = m
            except (ValueError, KeyError):
                pass
    return out


def pair_stats(mids_a, mids_b, label):
    """Stats for the *spread* a - b."""
    common = sorted(set(mids_a.keys()) & set(mids_b.keys()))
    spreads = [mids_a[t] - mids_b[t] for t in common]
    if not spreads:
        print(f"  {label}: no common ts")
        return
    mean = sum(spreads) / len(spreads)
    std = statistics.pstdev(spreads)
    p = 0
    for v in spreads:
        if abs(v - mean) > 2 * std:
            p += 1
    print(f"  {label:<35} mean={mean:>10.2f}  std={std:>8.2f}  |z|>2 ticks={p}/{len(spreads)} ({100*p/len(spreads):.1f}%)")
    return mean, std


def main():
    all_data = {}
    for day in (2, 3, 4):
        all_data[day] = load_mid(day)

    # Pool all 3 days' mids
    pooled = defaultdict(dict)
    for day, by_p in all_data.items():
        for p, by_ts in by_p.items():
            for ts, m in by_ts.items():
                # Use day*1_000_000 + ts as a unique time key
                pooled[p][day * 1_000_000 + ts] = m

    print("=== Pebbles pairs ===")
    pair_stats(pooled["PEBBLES_XL"], pooled["PEBBLES_XS"], "XL - XS")
    pair_stats(pooled["PEBBLES_XL"], pooled["PEBBLES_S"], "XL - S")
    pair_stats(pooled["PEBBLES_XL"], pooled["PEBBLES_M"], "XL - M")
    pair_stats(pooled["PEBBLES_XL"], pooled["PEBBLES_L"], "XL - L")

    print("\n=== Pebbles XL vs basket-of-others ===")
    common = sorted(set(pooled["PEBBLES_XL"].keys()) & set(pooled["PEBBLES_XS"].keys())
                    & set(pooled["PEBBLES_S"].keys()) & set(pooled["PEBBLES_M"].keys())
                    & set(pooled["PEBBLES_L"].keys()))
    spreads = [pooled["PEBBLES_XL"][t] + (pooled["PEBBLES_XS"][t] + pooled["PEBBLES_S"][t]
                + pooled["PEBBLES_M"][t] + pooled["PEBBLES_L"][t])/4 for t in common]
    mean = sum(spreads)/len(spreads); std = statistics.pstdev(spreads)
    print(f"  XL + avg(XS,S,M,L):    mean={mean:.2f}  std={std:.2f}")
    spreads2 = [pooled["PEBBLES_XL"][t] - (pooled["PEBBLES_XS"][t] + pooled["PEBBLES_S"][t]
                + pooled["PEBBLES_M"][t] + pooled["PEBBLES_L"][t])/4 for t in common]
    mean2 = sum(spreads2)/len(spreads2); std2 = statistics.pstdev(spreads2)
    print(f"  XL - avg(XS,S,M,L):    mean={mean2:.2f}  std={std2:.2f}")

    print("\n=== Snackpack pairs ===")
    pair_stats(pooled["SNACKPACK_CHOCOLATE"], pooled["SNACKPACK_VANILLA"], "CHOC - VAN (-0.92)")
    pair_stats(pooled["SNACKPACK_STRAWBERRY"], pooled["SNACKPACK_RASPBERRY"], "STRAW - RASP (-0.93)")
    pair_stats(pooled["SNACKPACK_PISTACHIO"], pooled["SNACKPACK_STRAWBERRY"], "PIS - STRAW (+0.91)")
    pair_stats(pooled["SNACKPACK_PISTACHIO"], pooled["SNACKPACK_RASPBERRY"], "PIS - RASP (-0.83)")

    print("\n=== Snackpack basket sums ===")
    common = sorted(set(pooled["SNACKPACK_CHOCOLATE"].keys()) & set(pooled["SNACKPACK_VANILLA"].keys()))
    s = [pooled["SNACKPACK_CHOCOLATE"][t] + pooled["SNACKPACK_VANILLA"][t] for t in common]
    print(f"  CHOC + VAN:           mean={sum(s)/len(s):.2f}  std={statistics.pstdev(s):.4f}  (sum stable?)")
    common2 = sorted(set(pooled["SNACKPACK_STRAWBERRY"].keys()) & set(pooled["SNACKPACK_RASPBERRY"].keys()))
    s2 = [pooled["SNACKPACK_STRAWBERRY"][t] + pooled["SNACKPACK_RASPBERRY"][t] for t in common2]
    print(f"  STRAW + RASP:         mean={sum(s2)/len(s2):.2f}  std={statistics.pstdev(s2):.4f}  (sum stable?)")


if __name__ == "__main__":
    main()
