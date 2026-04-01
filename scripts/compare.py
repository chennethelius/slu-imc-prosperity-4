#!/usr/bin/env python3
"""
compare.py — Side-by-side comparison of two backtest runs.

Usage:
    python scripts/compare.py runs/<run_a> runs/<run_b>

Outputs a diff table showing PnL, Sharpe, drawdown, and trade count deltas
per product. Designed for Claude to read and interpret.
"""

import json
import sys
from pathlib import Path


def load_metrics(run_dir: Path) -> dict:
    metrics_path = run_dir / "metrics.json"
    if not metrics_path.exists():
        print(f"WARNING: {metrics_path} not found, run analyze.py first", file=sys.stderr)
        return {}
    with open(metrics_path) as f:
        return json.load(f)


def fmt_delta(new: float, old: float, fmt: str = ",.2f") -> str:
    """Format a value with delta indicator."""
    delta = new - old
    if delta > 0:
        return f"{new:{fmt}} (+{delta:{fmt}})"
    elif delta < 0:
        return f"{new:{fmt}} ({delta:{fmt}})"
    else:
        return f"{new:{fmt}} (=)"


def compare(run_a: Path, run_b: Path):
    metrics_a = load_metrics(run_a)
    metrics_b = load_metrics(run_b)

    if not metrics_a or not metrics_b:
        sys.exit(1)

    pnl_a = metrics_a.get("pnl", {})
    pnl_b = metrics_b.get("pnl", {})
    trades_a = metrics_a.get("trades", {})
    trades_b = metrics_b.get("trades", {})

    print(f"{'=' * 80}")
    print(f"  COMPARISON: {run_a.name}  vs  {run_b.name}")
    print(f"  (A = baseline, B = new)")
    print(f"{'=' * 80}")
    print()

    # Total
    total_a = pnl_a.get("total_pnl", 0)
    total_b = pnl_b.get("total_pnl", 0)
    sharpe_a = pnl_a.get("total_sharpe", 0)
    sharpe_b = pnl_b.get("total_sharpe", 0)
    dd_a = pnl_a.get("total_max_drawdown", 0)
    dd_b = pnl_b.get("total_max_drawdown", 0)
    trades_total_a = trades_a.get("total_trades", 0)
    trades_total_b = trades_b.get("total_trades", 0)

    delta_pnl = total_b - total_a
    verdict = "IMPROVED" if delta_pnl > 0 else "REGRESSED" if delta_pnl < 0 else "UNCHANGED"

    print(f"  VERDICT: {verdict} ({delta_pnl:+,.2f} seashells)")
    print()
    print(f"  {'Metric':<24} {'A (baseline)':>16} {'B (new)':>16} {'Delta':>16}")
    print(f"  {'-' * 74}")
    print(f"  {'Total PnL':<24} {total_a:>16,.2f} {total_b:>16,.2f} {delta_pnl:>+16,.2f}")
    print(f"  {'Sharpe':<24} {sharpe_a:>16.4f} {sharpe_b:>16.4f} {sharpe_b - sharpe_a:>+16.4f}")
    print(f"  {'Max Drawdown':<24} {dd_a:>16,.2f} {dd_b:>16,.2f} {dd_b - dd_a:>+16,.2f}")
    print(f"  {'Total Trades':<24} {trades_total_a:>16,d} {trades_total_b:>16,d} {trades_total_b - trades_total_a:>+16,d}")

    # Per-product
    products_a = pnl_a.get("products", {})
    products_b = pnl_b.get("products", {})
    all_products = sorted(set(list(products_a.keys()) + list(products_b.keys())))

    if all_products:
        print()
        print(f"  {'Product':<24} {'PnL A':>12} {'PnL B':>12} {'Delta':>12} {'Sharpe A':>10} {'Sharpe B':>10}")
        print(f"  {'-' * 82}")
        for product in all_products:
            pa = products_a.get(product, {})
            pb = products_b.get(product, {})
            pnl_pa = pa.get("final_pnl", 0)
            pnl_pb = pb.get("final_pnl", 0)
            sha = pa.get("sharpe", 0)
            shb = pb.get("sharpe", 0)
            d = pnl_pb - pnl_pa
            marker = ">>>" if abs(d) > abs(total_a * 0.1) else "   "
            print(f"{marker}{product:<23} {pnl_pa:>12,.2f} {pnl_pb:>12,.2f} {d:>+12,.2f} {sha:>10.4f} {shb:>10.4f}")

    print()
    print(f"{'=' * 80}")


def main():
    if len(sys.argv) < 3:
        print("Usage: python compare.py <run_dir_a> <run_dir_b>", file=sys.stderr)
        sys.exit(1)

    run_a = Path(sys.argv[1]).resolve()
    run_b = Path(sys.argv[2]).resolve()

    if not run_a.is_dir():
        print(f"ERROR: {run_a} not found", file=sys.stderr)
        sys.exit(1)
    if not run_b.is_dir():
        print(f"ERROR: {run_b} not found", file=sys.stderr)
        sys.exit(1)

    compare(run_a, run_b)


if __name__ == "__main__":
    main()
