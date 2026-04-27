#!/usr/bin/env python3
"""Convert an IMC submission zip into an SLU-dashboard-style entry.

Reads the .py/.json/.log triple downloaded from the IMC API, derives
per-product PnL + trade summary, and writes them into a run dir under
backtest-results/ following the same layout the wrapper produces.
The CI deploy step picks these up on the next push.

Usage:
    python scripts/push_imc_submission_to_dashboard.py \
        backtests/imc-<id>-<name> \
        --strategy v62_add_5300 \
        --author jonathan-cheng19 \
        --commit <sha>
"""
import argparse
import csv
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


def parse_submission_log(log_path: Path):
    with log_path.open("r", encoding="utf-8") as f:
        d = json.load(f)
    return d


def aggregate_pnl(activity_lines):
    if len(activity_lines) < 2:
        return [], 0.0, {}, 0
    header = activity_lines[0].split(";")
    ts_idx = header.index("timestamp")
    prod_idx = header.index("product")
    pnl_idx = header.index("profit_and_loss")
    rows_by_ts = {}
    products = set()
    last_pnl_per_prod = {}
    for line in activity_lines[1:]:
        parts = line.split(";")
        if len(parts) <= pnl_idx:
            continue
        ts = int(parts[ts_idx])
        prod = parts[prod_idx]
        try:
            pnl = float(parts[pnl_idx])
        except ValueError:
            pnl = 0.0
        rows_by_ts.setdefault(ts, {})[prod] = pnl
        last_pnl_per_prod[prod] = pnl
        products.add(prod)
    products = sorted(products)
    out = [["timestamp", "total"] + products]
    for ts in sorted(rows_by_ts):
        row_pnl = rows_by_ts[ts]
        total = sum(row_pnl.values())
        out.append([ts, total] + [row_pnl.get(p, 0.0) for p in products])
    final_total = sum(last_pnl_per_prod.values())
    return out, final_total, last_pnl_per_prod, len(rows_by_ts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("submission_dir", type=Path,
                    help="Directory containing the IMC <id>.py/.json/.log triple")
    ap.add_argument("--strategy", required=True,
                    help="Logical strategy name (e.g. v62_add_5300)")
    ap.add_argument("--author", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--results-dir", type=Path, default=Path("backtest-results"))
    args = ap.parse_args()

    sub_dir = args.submission_dir.resolve()
    log_files = list(sub_dir.glob("*.log"))
    py_files = list(sub_dir.glob("*.py"))
    if not log_files:
        raise SystemExit(f"No .log file found in {sub_dir}")

    log_path = log_files[0]
    payload = parse_submission_log(log_path)
    activity_lines = payload.get("activitiesLog", "").strip().split("\n")
    trades = payload.get("tradeHistory", [])
    submission_id = payload.get("submissionId", log_path.stem)

    pnl_rows, total_pnl, last_pnl, tick_count = aggregate_pnl(activity_lines)
    own_trades = [t for t in trades if t.get("buyer") == "SUBMISSION" or t.get("seller") == "SUBMISSION"]

    # Build the SLU-dashboard run dir
    args.results_dir.mkdir(parents=True, exist_ok=True)
    sub_id = f"{args.author}_{args.strategy}_submission_imc-{submission_id[:8]}"
    out_dir = args.results_dir / sub_id
    out_dir.mkdir(exist_ok=True)

    # submission.log: copy as-is
    shutil.copy2(log_path, out_dir / "submission.log")

    # strategy.py: copy from submission dir if present
    if py_files:
        shutil.copy2(py_files[0], out_dir / "strategy.py")

    # activity.csv
    with (out_dir / "activity.csv").open("w", newline="", encoding="utf-8") as f:
        for line in activity_lines:
            f.write(line + "\n")

    # trades.csv (semicolon-delimited for SLU dashboard parser)
    with (out_dir / "trades.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["timestamp", "buyer", "seller", "symbol", "currency", "price", "quantity"])
        for t in trades:
            w.writerow([t.get("timestamp", 0), t.get("buyer", ""), t.get("seller", ""),
                        t.get("symbol", ""), t.get("currency", "XIREC"),
                        t.get("price", 0), t.get("quantity", 0)])

    # pnl_by_product.csv (semicolon-delimited)
    with (out_dir / "pnl_by_product.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        for row in pnl_rows:
            w.writerow(row)

    # metrics.json — IMPORTANT: dataset_path drives the dashboard's dataset field
    metrics = {
        "dataset_path": "submission.json",
        "day": "submission",
        "final_pnl_total": total_pnl,
        "final_pnl_by_product": last_pnl,
        "own_trade_count": len(own_trades),
        "tick_count": tick_count,
        "max_drawdown_abs": 0.0,
        "sharpe_ratio": 0.0,
        "imc_submission_id": submission_id,
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))

    # Append to new_results.json (collect_results-style manifest fragment)
    timestamp = datetime.now(timezone.utc).isoformat()
    entry = {
        "id": sub_id,
        "author": args.author,
        "strategy": args.strategy,
        "dataset": "submission.json",
        "day": "submission",
        "pnl": total_pnl,
        "pnl_by_product": last_pnl,
        "trades": len(own_trades),
        "ticks": tick_count,
        "timestamp": timestamp,
        "commit": args.commit[:7],
    }
    manifest_path = args.results_dir / "new_results.json"
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text())
    else:
        existing = []
    existing.append(entry)
    manifest_path.write_text(json.dumps(existing, indent=2))

    print(f"Wrote IMC submission entry: {sub_id}")
    print(f"  total PnL:     {total_pnl:>14,.2f}")
    print(f"  ticks:         {tick_count}")
    print(f"  own trades:    {len(own_trades)}")
    print(f"  output dir:    {out_dir}")
    print(f"  manifest:      {manifest_path}")


if __name__ == "__main__":
    main()
