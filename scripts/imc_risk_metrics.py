#!/usr/bin/env python3
"""Compute Sharpe + drawdown + PnL trajectory from an IMC submission log.

Aggregates per-tick total PnL across all products to produce a smooth
equity curve, then derives:

  - final_pnl: terminal PnL
  - tick_returns: per-tick deltas
  - sharpe_per_tick: mean / std of per-tick deltas (scaled by sqrt(N))
  - max_drawdown_abs: largest peak-to-trough decline
  - max_drawdown_pct: drawdown as % of running peak
  - day_pnl: per-day final PnL (smoothness across days)
"""
import json
import math
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

    pnl_by_tick = defaultdict(float)  # (day, ts) -> total PnL across products
    last_pnl_per_prod = {}             # product -> latest cumulative PnL

    last_seen = {}  # (day, ts) per product, just to fold non-emitting ticks

    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split(";")
        if len(parts) < len(header):
            continue
        try:
            day = int(parts[idx["day"]])
            ts = int(parts[idx["timestamp"]])
            prod = parts[idx["product"]]
            pnl = float(parts[idx["profit_and_loss"]]) if parts[idx["profit_and_loss"]] else 0.0
        except (ValueError, KeyError):
            continue
        last_pnl_per_prod[prod] = pnl
        # snapshot the running total at this tick
        pnl_by_tick[(day, ts)] = sum(last_pnl_per_prod.values())

    if not pnl_by_tick:
        print("no PnL points")
        return

    keys = sorted(pnl_by_tick.keys())
    series = [pnl_by_tick[k] for k in keys]

    final_pnl = series[-1]

    # Per-tick returns
    deltas = [series[i] - series[i - 1] for i in range(1, len(series))]
    if deltas:
        mu = sum(deltas) / len(deltas)
        var = sum((d - mu) ** 2 for d in deltas) / max(1, len(deltas) - 1)
        sd = math.sqrt(var)
        sharpe = (mu / sd) * math.sqrt(len(deltas)) if sd > 0 else 0.0
    else:
        mu = sd = sharpe = 0.0

    # Drawdowns
    peak = -math.inf
    max_dd_abs = 0.0
    max_dd_pct = 0.0
    for v in series:
        peak = max(peak, v)
        dd = peak - v
        if dd > max_dd_abs:
            max_dd_abs = dd
        if peak > 0:
            pct = dd / peak
            if pct > max_dd_pct:
                max_dd_pct = pct

    # Per-day final PnL
    day_pnl = {}
    for (day, _), v in pnl_by_tick.items():
        day_pnl[day] = v  # last value per day (since dict overwrite preserves last)
    day_keys = sorted(day_pnl)
    day_finals = [pnl_by_tick[max((d, ts) for (d, ts) in keys if d == day)] for day in day_keys]
    day_increments = []
    prev = 0.0
    for v in day_finals:
        day_increments.append(v - prev)
        prev = v

    # Per-day Sharpe (cross-day)
    if len(day_increments) > 1:
        d_mu = sum(day_increments) / len(day_increments)
        d_var = sum((x - d_mu) ** 2 for x in day_increments) / max(1, len(day_increments) - 1)
        d_sd = math.sqrt(d_var)
        d_sharpe = (d_mu / d_sd) * math.sqrt(len(day_increments)) if d_sd > 0 else 0.0
    else:
        d_sharpe = 0.0

    print(f"=== Risk metrics for {log.name} ===")
    print(f"final_pnl:           {final_pnl:>12,.1f}")
    print(f"per-tick mean:       {mu:>12,.4f}")
    print(f"per-tick std:        {sd:>12,.4f}")
    print(f"per-tick sharpe:     {sharpe:>12,.3f}")
    print(f"max_drawdown_abs:    {max_dd_abs:>12,.1f}")
    print(f"max_drawdown_pct:    {max_dd_pct:>12.2%}")
    print(f"per-day PnL:")
    for d, finalv, inc in zip(day_keys, day_finals, day_increments):
        print(f"  day {d}: cum={finalv:>10,.1f}  +{inc:>10,.1f}")
    print(f"cross-day sharpe:    {d_sharpe:>12,.3f}")


if __name__ == "__main__":
    main()
