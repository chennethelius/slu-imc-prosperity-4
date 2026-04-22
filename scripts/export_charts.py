#!/usr/bin/env python3
"""
export_charts.py — Generate matplotlib charts from backtest data for Claude to read.

Produces PNGs that Claude can interpret as images, complementing the
Highcharts visualizer with static exports.

Usage:
    python scripts/export_charts.py runs/<run_id>

Outputs PNGs to runs/<run_id>/charts/
"""

import sys
from collections import defaultdict
from pathlib import Path

from _loaders import load_activity_csv, load_trades_csv, get_pnl, get_mid, get_bid1, get_ask1

try:
    import matplotlib
    matplotlib.use("Agg")  # Non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
except ImportError:
    print("ERROR: matplotlib required. Install with: pip install matplotlib", file=sys.stderr)
    sys.exit(1)


# ── Style ──────────────────────────────────────────────────────────────────

plt.rcParams.update({
    "figure.figsize": (14, 6),
    "figure.dpi": 150,
    "figure.facecolor": "#1a1a2e",
    "axes.facecolor": "#16213e",
    "axes.edgecolor": "#444",
    "axes.labelcolor": "#ccc",
    "axes.grid": True,
    "grid.color": "#333",
    "grid.alpha": 0.5,
    "text.color": "#ccc",
    "xtick.color": "#999",
    "ytick.color": "#999",
    "legend.facecolor": "#1a1a2e",
    "legend.edgecolor": "#444",
    "font.family": "monospace",
    "font.size": 10,
})

COLORS = ["#00d4aa", "#ff6b6b", "#4ecdc4", "#f7dc6f", "#bb8fce", "#85c1e9", "#f0b27a", "#82e0aa"]
BID_COLOR = "#27ae60"
ASK_COLOR = "#c0392b"


def plot_pnl(activity: list[dict], charts_dir: Path):
    """PnL over time, per product + total."""
    by_product = defaultdict(list)
    total_by_ts = defaultdict(float)

    for row in activity:
        product = row.get("product", "UNKNOWN")
        ts = int(row.get("timestamp", 0))
        pnl = get_pnl(row)
        by_product[product].append((ts, pnl))
        total_by_ts[ts] += pnl

    fig, ax = plt.subplots()
    ax.set_title("Profit & Loss Over Time", fontsize=14, fontweight="bold")
    ax.set_xlabel("Timestamp")
    ax.set_ylabel("PnL (seashells)")

    # Total line (thick)
    total_sorted = sorted(total_by_ts.items())
    if total_sorted:
        ax.plot([t[0] for t in total_sorted], [t[1] for t in total_sorted],
                color="#ffffff", linewidth=2.5, label="TOTAL", zorder=10)

    # Per-product lines (dashed)
    for i, (product, data) in enumerate(sorted(by_product.items())):
        data.sort()
        ax.plot([d[0] for d in data], [d[1] for d in data],
                color=COLORS[i % len(COLORS)], linewidth=1.2, linestyle="--",
                label=product, alpha=0.8)

    ax.axhline(y=0, color="#666", linewidth=0.5, linestyle="-")
    ax.legend(loc="upper left", fontsize=8)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    fig.tight_layout()
    fig.savefig(charts_dir / "pnl.png")
    plt.close(fig)
    print(f"  -> pnl.png")


def plot_prices(activity: list[dict], charts_dir: Path):
    """Mid price + bid/ask spread per product."""
    by_product = defaultdict(list)

    for row in activity:
        product = row.get("product", "UNKNOWN")
        ts = int(row.get("timestamp", 0))
        by_product[product].append((ts, get_mid(row), get_bid1(row), get_ask1(row)))

    for i, (product, data) in enumerate(sorted(by_product.items())):
        data.sort()
        timestamps = [d[0] for d in data]
        mids = [d[1] for d in data]
        bids = [d[2] for d in data]
        asks = [d[3] for d in data]

        fig, ax = plt.subplots()
        ax.set_title(f"{product} — Price", fontsize=14, fontweight="bold")
        ax.set_xlabel("Timestamp")
        ax.set_ylabel("Price")

        ax.plot(timestamps, mids, color="#ffffff", linewidth=1.5, label="Mid", zorder=5)
        if any(b > 0 for b in bids):
            ax.plot(timestamps, bids, color=BID_COLOR, linewidth=0.8, alpha=0.6, label="Bid1")
        if any(a > 0 for a in asks):
            ax.plot(timestamps, asks, color=ASK_COLOR, linewidth=0.8, alpha=0.6, label="Ask1")
            # Shade the spread
            ax.fill_between(timestamps, bids, asks, alpha=0.1, color="#888")

        ax.legend(loc="upper left", fontsize=8)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
        fig.tight_layout()

        safe_name = product.lower().replace(" ", "_")
        fig.savefig(charts_dir / f"price_{safe_name}.png")
        plt.close(fig)
        print(f"  -> price_{safe_name}.png")


def plot_trades_scatter(activity: list[dict], trades: list[dict], charts_dir: Path):
    """Scatter plot of trade executions on price chart, per product."""
    # Build mid price series
    mid_by_product = defaultdict(list)
    for row in activity:
        product = row.get("product", "UNKNOWN")
        ts = int(row.get("timestamp", 0))
        mid_by_product[product].append((ts, get_mid(row)))

    # Group trades
    trades_by_product = defaultdict(list)
    for t in trades:
        symbol = t.get("symbol", t.get("product", "UNKNOWN"))
        ts = int(t.get("timestamp", 0))
        price = float(t.get("price", 0))
        qty = int(t.get("quantity", 0))
        buyer = t.get("buyer", "")
        seller = t.get("seller", "")

        is_my_buy = "SUBMISSION" in buyer
        is_my_sell = "SUBMISSION" in seller
        side = "buy" if is_my_buy else "sell" if is_my_sell else "other"

        trades_by_product[symbol].append((ts, price, qty, side))

    for product in sorted(set(list(mid_by_product.keys()) + list(trades_by_product.keys()))):
        mids = sorted(mid_by_product.get(product, []))
        product_trades = trades_by_product.get(product, [])

        if not mids and not product_trades:
            continue

        fig, ax = plt.subplots()
        ax.set_title(f"{product} — Trade Executions", fontsize=14, fontweight="bold")
        ax.set_xlabel("Timestamp")
        ax.set_ylabel("Price")

        # Mid price line
        if mids:
            ax.plot([m[0] for m in mids], [m[1] for m in mids],
                    color="#555", linewidth=0.8, alpha=0.5, label="Mid")

        # Trades as scatter
        buys = [(t[0], t[1], abs(t[2])) for t in product_trades if t[3] == "buy"]
        sells = [(t[0], t[1], abs(t[2])) for t in product_trades if t[3] == "sell"]

        if buys:
            sizes = [min(b[2] * 3, 80) for b in buys]
            ax.scatter([b[0] for b in buys], [b[1] for b in buys],
                       c=BID_COLOR, s=sizes, marker="^", alpha=0.7,
                       label=f"Buys ({len(buys)})", zorder=10, edgecolors="white", linewidth=0.3)
        if sells:
            sizes = [min(s[2] * 3, 80) for s in sells]
            ax.scatter([s[0] for s in sells], [s[1] for s in sells],
                       c=ASK_COLOR, s=sizes, marker="v", alpha=0.7,
                       label=f"Sells ({len(sells)})", zorder=10, edgecolors="white", linewidth=0.3)

        ax.legend(loc="upper left", fontsize=8)
        fig.tight_layout()

        safe_name = product.lower().replace(" ", "_")
        fig.savefig(charts_dir / f"trades_{safe_name}.png")
        plt.close(fig)
        print(f"  -> trades_{safe_name}.png")


def main():
    if len(sys.argv) < 2:
        print("Usage: python export_charts.py <run_directory>", file=sys.stderr)
        sys.exit(1)

    run_dir = Path(sys.argv[1]).resolve()
    if not run_dir.is_dir():
        print(f"ERROR: {run_dir} not found", file=sys.stderr)
        sys.exit(1)

    charts_dir = run_dir / "charts"
    charts_dir.mkdir(exist_ok=True)

    activity = load_activity_csv(run_dir)
    trades = load_trades_csv(run_dir)

    print(f"Exporting charts for {run_dir.name}...")

    if activity:
        plot_pnl(activity, charts_dir)
        plot_prices(activity, charts_dir)
        if trades:
            plot_trades_scatter(activity, trades, charts_dir)
    else:
        print("  No activity data found — skipping chart export.")
        print("  Run analyze.py first if you haven't already.")

    print(f"Done. Charts saved to {charts_dir}")


if __name__ == "__main__":
    main()
