#!/usr/bin/env python3
"""
visualize.py — Terminal-based charts for backtest results.

Usage:
    python3 scripts/visualize.py backtester/runs/<run_id>
    python3 scripts/visualize.py backtester/runs/<run_id> --product TOMATOES
    python3 scripts/visualize.py backtester/runs/<run_id> --depth  # order book at a timestamp

Renders PnL curves, price series, and spread analysis directly in the
terminal using plotext. No browser, no window switching.
"""

import sys
from collections import defaultdict
from pathlib import Path

from _loaders import load_run, get_pnl, get_mid, get_bid1, get_ask1

try:
    import plotext as plt
except ImportError:
    print("ERROR: pip install plotext", file=sys.stderr)
    sys.exit(1)


def chart_pnl(activity: list[dict], product_filter: str | None = None):
    """PnL over time — total + per product."""
    by_product = defaultdict(list)
    total_by_ts = defaultdict(float)

    for row in activity:
        product = row.get("product", "")
        ts = int(row.get("timestamp", 0))
        pnl = get_pnl(row)
        by_product[product].append((ts, pnl))
        total_by_ts[ts] += pnl

    plt.clear_figure()
    plt.theme("dark")
    plt.title("Profit & Loss")
    plt.xlabel("Timestamp")
    plt.ylabel("PnL (seashells)")

    if product_filter:
        # Single product
        if product_filter not in by_product:
            print(f"Product '{product_filter}' not found. Available: {sorted(by_product.keys())}")
            return
        data = sorted(by_product[product_filter])
        plt.plot([d[0] for d in data], [d[1] for d in data], label=product_filter)
    else:
        # Total + all products
        total = sorted(total_by_ts.items())
        plt.plot([t[0] for t in total], [t[1] for t in total], label="TOTAL")
        for product in sorted(by_product.keys()):
            data = sorted(by_product[product])
            plt.plot([d[0] for d in data], [d[1] for d in data], label=product)

    plt.show()


def chart_price(activity: list[dict], product_filter: str | None = None):
    """Mid price + bid/ask spread per product."""
    by_product = defaultdict(list)
    for row in activity:
        product = row.get("product", "")
        ts = int(row.get("timestamp", 0))
        by_product[product].append((ts, get_mid(row), get_bid1(row), get_ask1(row)))

    products = [product_filter] if product_filter else sorted(by_product.keys())

    for product in products:
        if product not in by_product:
            print(f"Product '{product}' not found.")
            continue

        data = sorted(by_product[product])
        timestamps = [d[0] for d in data]
        mids = [d[1] for d in data]
        bids = [d[2] for d in data]
        asks = [d[3] for d in data]

        plt.clear_figure()
        plt.theme("dark")
        plt.title(f"{product} — Price")
        plt.xlabel("Timestamp")
        plt.ylabel("Price")

        plt.plot(timestamps, mids, label="Mid")
        if any(b > 0 for b in bids):
            plt.plot(timestamps, bids, label="Bid1")
        if any(a > 0 for a in asks):
            plt.plot(timestamps, asks, label="Ask1")

        plt.show()
        print()


def chart_spread(activity: list[dict], product_filter: str | None = None):
    """Bid-ask spread over time."""
    by_product = defaultdict(list)
    for row in activity:
        product = row.get("product", "")
        ts = int(row.get("timestamp", 0))
        bid = get_bid1(row)
        ask = get_ask1(row)
        if bid > 0 and ask > 0:
            by_product[product].append((ts, ask - bid))

    products = [product_filter] if product_filter else sorted(by_product.keys())

    for product in products:
        if product not in by_product:
            continue

        data = sorted(by_product[product])
        if not data:
            continue

        plt.clear_figure()
        plt.theme("dark")
        plt.title(f"{product} — Bid-Ask Spread")
        plt.xlabel("Timestamp")
        plt.ylabel("Spread")

        plt.plot([d[0] for d in data], [d[1] for d in data], label="Spread")
        plt.show()
        print()


def chart_all(activity: list[dict], product_filter: str | None = None):
    """Run all charts in sequence."""
    chart_pnl(activity, product_filter)
    print()
    chart_price(activity, product_filter)
    chart_spread(activity, product_filter)


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: python3 visualize.py <run_dir> [--product NAME] [--pnl|--price|--spread]", file=sys.stderr)
        sys.exit(1)

    run_dir = Path(args[0]).resolve()
    if not run_dir.is_dir():
        print(f"ERROR: {run_dir} not found", file=sys.stderr)
        sys.exit(1)

    # Parse flags
    product_filter = None
    chart_type = "all"
    i = 1
    while i < len(args):
        if args[i] == "--product" and i + 1 < len(args):
            product_filter = args[i + 1]
            i += 2
        elif args[i] == "--pnl":
            chart_type = "pnl"
            i += 1
        elif args[i] == "--price":
            chart_type = "price"
            i += 1
        elif args[i] == "--spread":
            chart_type = "spread"
            i += 1
        else:
            i += 1

    metrics, activity = load_run(run_dir)
    if not activity:
        print(f"ERROR: No activity data in {run_dir}/submission.log", file=sys.stderr)
        sys.exit(1)

    products = sorted(set(r.get("product", "") for r in activity))
    print(f"Run: {run_dir.name}")
    print(f"Products: {', '.join(products)}")
    if metrics:
        total = metrics.get("final_pnl_total", 0)
        print(f"Total PnL: {total:+,.2f} seashells")
    print()

    if chart_type == "pnl":
        chart_pnl(activity, product_filter)
    elif chart_type == "price":
        chart_price(activity, product_filter)
    elif chart_type == "spread":
        chart_spread(activity, product_filter)
    else:
        chart_all(activity, product_filter)


if __name__ == "__main__":
    main()
