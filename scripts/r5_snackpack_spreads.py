#!/usr/bin/env python3
"""Investigate SNACKPACK pair-trading viability.

Computes for each cointegrated pair:
  - rolling spread (sum or diff depending on corr sign)
  - spread mean and stddev per day
  - max deviation from mean in each day (potential trade size)
  - bid-ask spreads of the products (transaction cost)

A pair is tradeable if max_deviation > 2 * (combined transaction cost).
"""
import csv
from collections import defaultdict
from pathlib import Path
import statistics


def load_book(path):
    """Returns {product: [(ts, bid, ask, mid)]}."""
    out = defaultdict(list)
    with open(path, newline="") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        idx = {c: i for i, c in enumerate(header)}
        for row in reader:
            if not row or len(row) < len(header):
                continue
            try:
                ts = int(row[idx["timestamp"]])
                prod = row[idx["product"]]
                bid_s = row[idx["bid_price_1"]]
                ask_s = row[idx["ask_price_1"]]
                mid_s = row[idx["mid_price"]]
                if not (bid_s and ask_s and mid_s):
                    continue
                out[prod].append((ts, float(bid_s), float(ask_s), float(mid_s)))
            except (ValueError, KeyError):
                continue
    return out


def main():
    repo = Path(__file__).resolve().parent.parent
    data_root = repo / "datasets_extra" / "round5_data"
    days = [2, 3, 4]

    # Pairs of interest
    pairs = [
        ("SNACKPACK_CHOCOLATE", "SNACKPACK_VANILLA",   "+",  "sum (anticorr)"),
        ("SNACKPACK_RASPBERRY", "SNACKPACK_STRAWBERRY", "+", "sum (anticorr)"),
        ("SNACKPACK_PISTACHIO", "SNACKPACK_RASPBERRY", "+",  "sum (anticorr)"),
        ("SNACKPACK_PISTACHIO", "SNACKPACK_STRAWBERRY", "-", "diff (poscorr)"),
    ]

    for a, b, op, label in pairs:
        print(f"\n=== {a} {op} {b}  [{label}] ===")
        for d in days:
            books = load_book(data_root / f"prices_round_5_day_{d}.csv")
            seq_a = books.get(a, [])
            seq_b = books.get(b, [])
            n = min(len(seq_a), len(seq_b))
            if n < 100:
                print(f"  day {d}: insufficient data ({n} pts)")
                continue
            spreads = []
            avg_book_a = []
            avg_book_b = []
            for i in range(n):
                ts_a, bid_a, ask_a, mid_a = seq_a[i]
                ts_b, bid_b, ask_b, mid_b = seq_b[i]
                if op == "+":
                    spreads.append(mid_a + mid_b)
                else:
                    spreads.append(mid_a - mid_b)
                avg_book_a.append(ask_a - bid_a)
                avg_book_b.append(ask_b - bid_b)
            mu = statistics.mean(spreads)
            sd = statistics.stdev(spreads)
            max_dev = max(abs(s - mu) for s in spreads)
            book_a = statistics.mean(avg_book_a)
            book_b = statistics.mean(avg_book_b)
            txn_cost = book_a / 2 + book_b / 2  # half-spread each side
            n_above_2sd = sum(1 for s in spreads if abs(s - mu) > 2 * sd)
            print(f"  day {d}: mean={mu:.1f}  sd={sd:.2f}  max_dev={max_dev:.1f}  "
                  f"book_a={book_a:.1f}  book_b={book_b:.1f}  "
                  f"txn_cost={txn_cost:.1f}  ticks_above_2sd={n_above_2sd}")
            print(f"          tradeable if max_dev > 2*txn_cost ({2*txn_cost:.1f}): {'YES' if max_dev > 2*txn_cost else 'no'}")


if __name__ == "__main__":
    main()
