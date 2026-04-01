#!/usr/bin/env python3
"""
analyze.py — Parse backtest outputs into a structured summary.

Usage:
    python scripts/analyze.py runs/<run_id>

Reads metrics.json, trades.csv, pnl_by_product.csv, activity.csv from the run
directory and produces a human-readable + Claude-readable summary.
"""

import csv
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path


def load_json(path: Path) -> dict | None:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def load_csv(path: Path) -> list[dict] | None:
    if path.exists():
        with open(path) as f:
            return list(csv.DictReader(f))
    return None


def load_csv_semicolon(path: Path) -> list[dict] | None:
    """Load semicolon-delimited CSV (activity log format)."""
    if path.exists():
        with open(path) as f:
            return list(csv.DictReader(f, delimiter=";"))
    return None


def compute_sharpe(pnl_series: list[float], risk_free: float = 0.0) -> float:
    """Annualized Sharpe from a PnL time series (tick-level returns)."""
    if len(pnl_series) < 2:
        return 0.0
    returns = [pnl_series[i] - pnl_series[i - 1] for i in range(1, len(pnl_series))]
    if not returns:
        return 0.0
    mean_ret = sum(returns) / len(returns)
    var = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
    std = math.sqrt(var) if var > 0 else 0.0
    if std == 0:
        return float("inf") if mean_ret > 0 else float("-inf") if mean_ret < 0 else 0.0
    # Annualize: assume 10000 ticks/day, ~252 trading days
    ticks_per_year = 10_000 * 252
    return (mean_ret / std) * math.sqrt(ticks_per_year)


def compute_max_drawdown(pnl_series: list[float]) -> float:
    """Maximum drawdown from peak PnL."""
    if not pnl_series:
        return 0.0
    peak = pnl_series[0]
    max_dd = 0.0
    for pnl in pnl_series:
        peak = max(peak, pnl)
        dd = peak - pnl
        max_dd = max(max_dd, dd)
    return max_dd


def compute_win_rate(trades: list[dict]) -> dict:
    """Win rate and avg win/loss from trades."""
    if not trades:
        return {"win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "total": 0}

    # Group trades by symbol and compute per-trade PnL is complex without fill prices
    # Instead, just count buys vs sells and average prices
    total = len(trades)
    return {"total_trades": total}


def analyze_activity_log(rows: list[dict]) -> dict:
    """Analyze the activity log for per-product PnL and summary stats."""
    products = defaultdict(list)
    total_pnl_series = defaultdict(float)

    for row in rows:
        product = row.get("product", "UNKNOWN")
        timestamp = int(row.get("timestamp", 0))
        pnl = float(row.get("profitLoss", 0) or 0)
        mid = float(row.get("midPrice", 0) or 0)

        products[product].append({
            "timestamp": timestamp,
            "pnl": pnl,
            "mid": mid,
        })
        total_pnl_series[timestamp] = total_pnl_series.get(timestamp, 0) + pnl

    result = {}
    for product, entries in sorted(products.items()):
        pnl_series = [e["pnl"] for e in entries]
        mid_prices = [e["mid"] for e in entries if e["mid"] != 0]

        final_pnl = pnl_series[-1] if pnl_series else 0.0
        sharpe = compute_sharpe(pnl_series)
        max_dd = compute_max_drawdown(pnl_series)

        result[product] = {
            "final_pnl": round(final_pnl, 2),
            "sharpe": round(sharpe, 4),
            "max_drawdown": round(max_dd, 2),
            "ticks": len(entries),
            "price_range": {
                "min": round(min(mid_prices), 2) if mid_prices else 0,
                "max": round(max(mid_prices), 2) if mid_prices else 0,
                "mean": round(sum(mid_prices) / len(mid_prices), 2) if mid_prices else 0,
            },
        }

    # Total
    sorted_timestamps = sorted(total_pnl_series.keys())
    total_series = [total_pnl_series[t] for t in sorted_timestamps]
    total_final = total_series[-1] if total_series else 0.0

    return {
        "total_pnl": round(total_final, 2),
        "total_sharpe": round(compute_sharpe(total_series), 4),
        "total_max_drawdown": round(compute_max_drawdown(total_series), 2),
        "products": result,
    }


def analyze_trades(rows: list[dict]) -> dict:
    """Analyze trades for execution quality metrics."""
    if not rows:
        return {"total_trades": 0}

    by_product = defaultdict(list)
    for row in rows:
        symbol = row.get("symbol", row.get("product", "UNKNOWN"))
        by_product[symbol].append(row)

    result = {}
    total_trades = 0
    for product, trades in sorted(by_product.items()):
        buys = [t for t in trades if int(t.get("quantity", 0)) > 0 or t.get("side") == "BUY"]
        sells = [t for t in trades if int(t.get("quantity", 0)) < 0 or t.get("side") == "SELL"]

        result[product] = {
            "total": len(trades),
            "buys": len(buys),
            "sells": len(sells),
        }
        total_trades += len(trades)

    return {"total_trades": total_trades, "by_product": result}


def format_summary(run_dir: Path, activity_stats: dict, trade_stats: dict) -> str:
    """Format a clean summary for terminal output and Claude reading."""
    lines = []
    lines.append(f"{'=' * 60}")
    lines.append(f"  BACKTEST SUMMARY: {run_dir.name}")
    lines.append(f"{'=' * 60}")
    lines.append("")

    # Total PnL
    total = activity_stats.get("total_pnl", 0)
    direction = "PROFIT" if total >= 0 else "LOSS"
    lines.append(f"  Total PnL:        {total:>12,.2f} seashells ({direction})")
    lines.append(f"  Sharpe Ratio:     {activity_stats.get('total_sharpe', 0):>12.4f}")
    lines.append(f"  Max Drawdown:     {activity_stats.get('total_max_drawdown', 0):>12,.2f}")
    lines.append(f"  Total Trades:     {trade_stats.get('total_trades', 0):>12,d}")
    lines.append("")

    # Per-product breakdown
    products = activity_stats.get("products", {})
    if products:
        lines.append(f"  {'Product':<28} {'PnL':>12} {'Sharpe':>10} {'MaxDD':>12} {'Ticks':>8}")
        lines.append(f"  {'-' * 72}")
        for product, stats in sorted(products.items(), key=lambda x: x[1]["final_pnl"], reverse=True):
            pnl = stats["final_pnl"]
            sharpe = stats["sharpe"]
            max_dd = stats["max_drawdown"]
            ticks = stats["ticks"]
            marker = "+" if pnl >= 0 else ""
            lines.append(f"  {product:<28} {marker}{pnl:>11,.2f} {sharpe:>10.4f} {max_dd:>12,.2f} {ticks:>8,d}")

    # Trade breakdown
    trade_products = trade_stats.get("by_product", {})
    if trade_products:
        lines.append("")
        lines.append(f"  {'Product':<28} {'Trades':>8} {'Buys':>8} {'Sells':>8}")
        lines.append(f"  {'-' * 54}")
        for product, stats in sorted(trade_products.items()):
            lines.append(f"  {product:<28} {stats['total']:>8,d} {stats['buys']:>8,d} {stats['sells']:>8,d}")

    lines.append("")
    lines.append(f"{'=' * 60}")
    lines.append(f"  Files: {run_dir}")
    lines.append(f"{'=' * 60}")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze.py <run_directory>", file=sys.stderr)
        sys.exit(1)

    run_dir = Path(sys.argv[1]).resolve()
    if not run_dir.is_dir():
        print(f"ERROR: {run_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Try to load activity log (semicolon-delimited)
    activity_rows = load_csv_semicolon(run_dir / "activity.csv")

    # Fallback: try comma-delimited
    if not activity_rows:
        activity_rows = load_csv(run_dir / "activity.csv")

    # Also try pnl_by_product.csv
    if not activity_rows:
        activity_rows = load_csv(run_dir / "pnl_by_product.csv")

    activity_stats = analyze_activity_log(activity_rows) if activity_rows else {
        "total_pnl": 0, "total_sharpe": 0, "total_max_drawdown": 0, "products": {}
    }

    # Load trades
    trade_rows = load_csv(run_dir / "trades.csv")
    trade_stats = analyze_trades(trade_rows) if trade_rows else {"total_trades": 0}

    # Merge with metrics.json if it exists (backtester may provide pre-computed metrics)
    metrics = load_json(run_dir / "metrics.json")
    if metrics:
        if "total_pnl" in metrics and activity_stats["total_pnl"] == 0:
            activity_stats["total_pnl"] = metrics["total_pnl"]
        if "products" in metrics:
            for product, data in metrics["products"].items():
                if product not in activity_stats["products"]:
                    activity_stats["products"][product] = data

    # Write structured metrics
    combined_metrics = {
        "run_id": run_dir.name,
        "pnl": activity_stats,
        "trades": trade_stats,
    }
    with open(run_dir / "metrics.json", "w") as f:
        json.dump(combined_metrics, f, indent=2)

    # Print summary
    summary = format_summary(run_dir, activity_stats, trade_stats)
    print(summary)


if __name__ == "__main__":
    main()
