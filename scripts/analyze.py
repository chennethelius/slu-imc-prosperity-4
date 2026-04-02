#!/usr/bin/env python3
"""
analyze.py — Parse backtest outputs into a structured summary.

Usage:
    python scripts/analyze.py runs/<run_id>
    python scripts/analyze.py backtester/runs/<run_id>

Reads metrics.json and submission.log from the run directory. Extracts the
activitiesLog CSV embedded in submission.log for per-product PnL, Sharpe,
and drawdown analysis.
"""

import csv
import io
import json
import math
import sys
from collections import defaultdict
from pathlib import Path


def load_json(path: Path) -> dict | None:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def parse_activity_from_submission_log(path: Path) -> list[dict] | None:
    """Extract the activitiesLog CSV from a submission.log JSON file."""
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    csv_text = data.get("activitiesLog", "")
    if not csv_text:
        return None
    return list(csv.DictReader(io.StringIO(csv_text), delimiter=";"))


def compute_sharpe(pnl_series: list[float]) -> float:
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
        max_dd = max(max_dd, peak - pnl)
    return max_dd


def analyze_activity_log(rows: list[dict]) -> dict:
    """Analyze the activity log for per-product PnL and summary stats."""
    products = defaultdict(list)
    total_pnl_series = defaultdict(float)

    # The backtester uses these column names:
    #   day, timestamp, product, bid_price_1, bid_volume_1, ..., mid_price, profit_and_loss
    for row in rows:
        product = row.get("product", "UNKNOWN")
        timestamp = int(row.get("timestamp", 0))
        # Handle both column name conventions
        pnl = float(row.get("profit_and_loss", row.get("profitLoss", 0)) or 0)
        mid = float(row.get("mid_price", row.get("midPrice", 0)) or 0)

        products[product].append({"timestamp": timestamp, "pnl": pnl, "mid": mid})
        total_pnl_series[timestamp] = total_pnl_series.get(timestamp, 0) + pnl

    result = {}
    for product, entries in sorted(products.items()):
        pnl_series = [e["pnl"] for e in entries]
        mid_prices = [e["mid"] for e in entries if e["mid"] != 0]

        result[product] = {
            "final_pnl": round(pnl_series[-1], 2) if pnl_series else 0.0,
            "sharpe": round(compute_sharpe(pnl_series), 4),
            "max_drawdown": round(compute_max_drawdown(pnl_series), 2),
            "ticks": len(entries),
            "price_range": {
                "min": round(min(mid_prices), 2) if mid_prices else 0,
                "max": round(max(mid_prices), 2) if mid_prices else 0,
                "mean": round(sum(mid_prices) / len(mid_prices), 2) if mid_prices else 0,
            },
        }

    sorted_ts = sorted(total_pnl_series.keys())
    total_series = [total_pnl_series[t] for t in sorted_ts]

    return {
        "total_pnl": round(total_series[-1], 2) if total_series else 0.0,
        "total_sharpe": round(compute_sharpe(total_series), 4),
        "total_max_drawdown": round(compute_max_drawdown(total_series), 2),
        "products": result,
    }


def format_summary(run_dir: Path, metrics: dict | None, activity_stats: dict) -> str:
    lines = []
    lines.append(f"{'=' * 60}")
    lines.append(f"  BACKTEST SUMMARY: {run_dir.name}")
    lines.append(f"{'=' * 60}")
    lines.append("")

    # Use backtester metrics.json for authoritative PnL
    if metrics:
        total = metrics.get("final_pnl_total", activity_stats.get("total_pnl", 0))
        trades = metrics.get("own_trade_count", 0)
        ticks = metrics.get("tick_count", 0)
        trader = metrics.get("trader_path", "unknown")
        dataset = metrics.get("dataset_path", "unknown")
        lines.append(f"  Trader:           {trader}")
        lines.append(f"  Dataset:          {dataset}")
        lines.append(f"  Ticks:            {ticks:>12,d}")
    else:
        total = activity_stats.get("total_pnl", 0)
        trades = 0

    direction = "PROFIT" if total >= 0 else "LOSS"
    lines.append(f"  Total PnL:        {total:>12,.2f} seashells ({direction})")
    lines.append(f"  Sharpe Ratio:     {activity_stats.get('total_sharpe', 0):>12.4f}")
    lines.append(f"  Max Drawdown:     {activity_stats.get('total_max_drawdown', 0):>12,.2f}")
    if trades:
        lines.append(f"  Own Trades:       {trades:>12,d}")
    lines.append("")

    # Per-product breakdown
    products = activity_stats.get("products", {})
    # Merge in backtester per-product PnL if activity log was empty
    if not products and metrics and "final_pnl_by_product" in metrics:
        for product, pnl in metrics["final_pnl_by_product"].items():
            products[product] = {"final_pnl": pnl, "sharpe": 0, "max_drawdown": 0, "ticks": 0}

    if products:
        lines.append(f"  {'Product':<28} {'PnL':>12} {'Sharpe':>10} {'MaxDD':>12} {'Ticks':>8}")
        lines.append(f"  {'-' * 72}")
        for product, stats in sorted(products.items(), key=lambda x: x[1]["final_pnl"], reverse=True):
            pnl = stats["final_pnl"]
            sharpe = stats.get("sharpe", 0)
            max_dd = stats.get("max_drawdown", 0)
            ticks = stats.get("ticks", 0)
            marker = "+" if pnl >= 0 else ""
            lines.append(f"  {product:<28} {marker}{pnl:>11,.2f} {sharpe:>10.4f} {max_dd:>12,.2f} {ticks:>8,d}")

    # Price ranges
    products_with_prices = {k: v for k, v in products.items() if v.get("price_range", {}).get("mean", 0) > 0}
    if products_with_prices:
        lines.append("")
        lines.append(f"  {'Product':<28} {'Min':>10} {'Mean':>10} {'Max':>10}")
        lines.append(f"  {'-' * 60}")
        for product, stats in sorted(products_with_prices.items()):
            pr = stats["price_range"]
            lines.append(f"  {product:<28} {pr['min']:>10,.2f} {pr['mean']:>10,.2f} {pr['max']:>10,.2f}")

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

    # Load backtester metrics.json
    metrics = load_json(run_dir / "metrics.json")

    # Parse activity log from submission.log
    activity_rows = parse_activity_from_submission_log(run_dir / "submission.log")
    activity_stats = analyze_activity_log(activity_rows) if activity_rows else {
        "total_pnl": 0, "total_sharpe": 0, "total_max_drawdown": 0, "products": {}
    }

    # Print summary
    summary = format_summary(run_dir, metrics, activity_stats)
    print(summary)

    # Write summary.txt
    with open(run_dir / "summary.txt", "w") as f:
        f.write(summary)

    # Write enriched metrics (merge backtester metrics + our computed stats)
    enriched = {
        "run_id": run_dir.name,
        "backtester_metrics": metrics,
        "computed": activity_stats,
    }
    with open(run_dir / "enriched_metrics.json", "w") as f:
        json.dump(enriched, f, indent=2)


if __name__ == "__main__":
    main()
