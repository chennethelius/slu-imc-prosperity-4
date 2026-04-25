"""
Aggressor flow analysis for round 3.

For each trade we infer the aggressor side by comparing trade price to the
top-of-book at the same timestamp (closer to the ask = buyer aggressor;
closer to the bid = seller aggressor; exactly mid = ambiguous).

Then we score "informedness" of aggressor flow: when aggressors are net
buyers in tick t, does the mid drift up over the next K ticks? If so,
aggressor flow leads price — useful to lean with, dangerous to fade.

Reports per product:
  - Total trade count, total volume
  - Aggressor split (buyer% / seller% / ambiguous%)
  - Lead-lag correlation: aggressor net flow at t vs mid change at t+K
  - Mean future return conditional on aggressor direction
"""

import csv
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "backtester" / "datasets" / "round3"


def load_book_index() -> dict:
    """Return {(timestamp, product): (best_bid, best_ask, mid)}."""
    out = {}
    for day in (0, 1, 2):
        with (DATA / f"prices_round_3_day_{day}.csv").open() as f:
            for r in csv.DictReader(f, delimiter=";"):
                ts = int(r["timestamp"]) + day * 1_000_000
                if not (r["bid_price_1"] and r["ask_price_1"] and r["mid_price"]):
                    continue
                out[(ts, r["product"])] = (
                    float(r["bid_price_1"]),
                    float(r["ask_price_1"]),
                    float(r["mid_price"]),
                )
    return out


def load_trades() -> list[tuple[int, str, float, int]]:
    """Return [(timestamp, product, price, quantity), ...]."""
    rows = []
    for day in (0, 1, 2):
        with (DATA / f"trades_round_3_day_{day}.csv").open() as f:
            for r in csv.DictReader(f, delimiter=";"):
                rows.append((
                    int(r["timestamp"]) + day * 1_000_000,
                    r["symbol"],
                    float(r["price"]),
                    int(r["quantity"]),
                ))
    return rows


def classify_aggressor(price: float, bid: float, ask: float, mid: float) -> str:
    """Buyer aggressor if price >= ask (or above mid),
    seller aggressor if price <= bid (or below mid),
    ambiguous if exactly mid."""
    if price >= ask:
        return "buyer"
    if price <= bid:
        return "seller"
    if price > mid:
        return "buyer"
    if price < mid:
        return "seller"
    return "ambiguous"


def main():
    books = load_book_index()
    trades = load_trades()

    # Build per-product mid time series for forward-return lookups.
    mids_by_product: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for (ts, product), (_, _, mid) in books.items():
        mids_by_product[product].append((ts, mid))
    for p in mids_by_product:
        mids_by_product[p].sort()

    def future_return(product: str, ts: int, k: int) -> float | None:
        series = mids_by_product[product]
        # Binary search for the index of `ts`.
        lo, hi = 0, len(series)
        while lo < hi:
            mid_ix = (lo + hi) // 2
            if series[mid_ix][0] < ts:
                lo = mid_ix + 1
            else:
                hi = mid_ix
        if lo >= len(series) or series[lo][0] != ts:
            return None
        if lo + k >= len(series):
            return None
        return series[lo + k][1] - series[lo][1]

    # Group trades by product. For each, compute aggressor split and
    # informedness over forward windows of 1, 5, 20 ticks.
    by_product: dict[str, list] = defaultdict(list)
    for ts, product, price, qty in trades:
        book = books.get((ts, product))
        if not book:
            continue
        bid, ask, mid = book
        side = classify_aggressor(price, bid, ask, mid)
        by_product[product].append((ts, qty, side, price, mid))

    horizons = (1, 5, 20)
    print(f"{'product':<22}{'trades':>8}{'volume':>9}{'buyAgg':>8}{'sellAgg':>9}{'ambig':>8}"
          + "".join(f"{f'r({k})':>10}" for k in horizons)
          + "  signal")
    print("-" * 100)

    for product in sorted(by_product):
        rows = by_product[product]
        if not rows:
            continue
        total_trades = len(rows)
        total_vol = sum(r[1] for r in rows)
        agg_count = {"buyer": 0, "seller": 0, "ambiguous": 0}
        agg_vol = {"buyer": 0, "seller": 0, "ambiguous": 0}
        for _, qty, side, _, _ in rows:
            agg_count[side] += 1
            agg_vol[side] += qty

        # Forward-return analysis: mean future return given buyer-agg vs seller-agg.
        forward = {k: {"buyer": [], "seller": []} for k in horizons}
        for ts, _, side, _, _ in rows:
            if side == "ambiguous":
                continue
            for k in horizons:
                ret = future_return(product, ts, k)
                if ret is not None:
                    forward[k][side].append(ret)

        # "Informedness" = E[r | buyer-agg] - E[r | seller-agg].
        # Positive = aggressors trade in direction of upcoming move (informed).
        signals = []
        for k in horizons:
            b = forward[k]["buyer"]
            s = forward[k]["seller"]
            if len(b) < 5 or len(s) < 5:
                signals.append("-")
                continue
            edge = sum(b) / len(b) - sum(s) / len(s)
            signals.append(f"{edge:+.3f}")

        if any(s != "-" and float(s) > 0.5 for s in signals):
            verdict = "INFORMED (lean with)"
        elif any(s != "-" and float(s) < -0.5 for s in signals):
            verdict = "NOISE (fade)"
        else:
            verdict = "neutral"

        buy_pct = 100.0 * agg_vol["buyer"] / max(1, total_vol)
        sell_pct = 100.0 * agg_vol["seller"] / max(1, total_vol)
        amb_pct = 100.0 * agg_vol["ambiguous"] / max(1, total_vol)
        print(f"{product:<22}{total_trades:>8}{total_vol:>9}"
              f"{buy_pct:>7.1f}%{sell_pct:>8.1f}%{amb_pct:>7.1f}%"
              + "".join(f"{s:>10}" for s in signals)
              + f"  {verdict}")


if __name__ == "__main__":
    main()
