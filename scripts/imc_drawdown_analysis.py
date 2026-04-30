#!/usr/bin/env python3
"""Identify drawdown segments and the products causing them.

For each tick, sum PnL across products. Find the largest peak-to-trough
drawdown segment. Then break down which products contributed most to the
drawdown (largest negative PnL deltas during the segment).
"""
import json
import sys
from collections import defaultdict
from pathlib import Path


def main():
    log = Path(sys.argv[1])
    obj = json.loads(log.read_text(encoding="utf-8"))
    csv = obj["activitiesLog"]
    lines = csv.splitlines()
    header = lines[0].split(";")
    idx = {c: i for i, c in enumerate(header)}

    last_pnl_per_prod = {}
    pnl_by_tick = {}      # ts -> total
    prod_pnl_by_tick = defaultdict(dict)  # ts -> prod -> cumPnL
    timestamps = []

    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split(";")
        if len(parts) < len(header):
            continue
        try:
            ts = int(parts[idx["timestamp"]])
            prod = parts[idx["product"]]
            pnl = float(parts[idx["profit_and_loss"]]) if parts[idx["profit_and_loss"]] else 0.0
        except (ValueError, KeyError):
            continue
        last_pnl_per_prod[prod] = pnl
        if ts not in pnl_by_tick:
            timestamps.append(ts)
        pnl_by_tick[ts] = sum(last_pnl_per_prod.values())
        prod_pnl_by_tick[ts] = dict(last_pnl_per_prod)

    timestamps.sort()
    series = [pnl_by_tick[ts] for ts in timestamps]

    # Find max drawdown segment
    peak = -float("inf")
    peak_idx = 0
    max_dd = 0.0
    dd_start = 0
    dd_end = 0
    for i, v in enumerate(series):
        if v > peak:
            peak = v
            peak_idx = i
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
            dd_start = peak_idx
            dd_end = i

    print(f"Max drawdown: {max_dd:,.1f}")
    print(f"  peak at tick {timestamps[dd_start]} (cumPnL={series[dd_start]:,.1f})")
    print(f"  trough at tick {timestamps[dd_end]} (cumPnL={series[dd_end]:,.1f})")
    print(f"  duration: {timestamps[dd_end] - timestamps[dd_start]} ticks")

    if dd_end <= dd_start:
        return

    # Per-product PnL change during drawdown
    start_pnl = prod_pnl_by_tick[timestamps[dd_start]]
    end_pnl = prod_pnl_by_tick[timestamps[dd_end]]
    deltas = []
    for prod in start_pnl:
        delta = end_pnl.get(prod, start_pnl[prod]) - start_pnl[prod]
        deltas.append((prod, delta))
    deltas.sort(key=lambda x: x[1])

    print(f"\nTop drawdown contributors (most negative PnL change during drawdown):")
    print(f"{'product':<32} {'delta':>10}")
    for prod, d in deltas[:15]:
        print(f"  {prod:<32} {d:>10,.1f}")

    print(f"\nTop drawdown offsetters (most positive during same window):")
    for prod, d in reversed(deltas[-10:]):
        print(f"  {prod:<32} {d:>10,.1f}")


if __name__ == "__main__":
    main()
