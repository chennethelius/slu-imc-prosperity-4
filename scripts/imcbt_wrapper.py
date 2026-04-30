#!/usr/bin/env python3
"""Drop-in replacement for rust_backtester binary that calls imc-p4-bt.

Mimics the rust_backtester CLI used by .github/collect_results.py:
  --trader <path>
  --dataset <name>
  --day=<num>
  --queue-penetration <float>   (ignored — imc-p4-bt has its own model)
  --book-fill <true|false>      (ignored)
  --price-slippage-bps <float>  (ignored)
  --persist
  --artifact-mode <none|diagnostic|submission|full>
  --output-root <path>
  --products <off|summary|full>

Runs imc-p4-bt and writes the expected output files into <output-root>/
backtest-<run_id>/:
  metrics.json
  pnl_by_product.csv
  trades.csv
  activity.csv
  submission.log

This produces SLU-dashboard-compatible output where the PnL matches
imc-p4-bt's whole-day model rather than rust_backtester's restricted
matching. User-flagged: imc-p4-bt is the calibration that matches IMC
submission window full-day potential.
"""
import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _find_data_dir(repo_root: Path, dataset: str) -> Path:
    """Map a dataset name (e.g. 'round4') to the matching prices/trades dir."""
    candidates = [
        repo_root / "datasets_extra" / f"{dataset}_data",
        repo_root / "datasets_extra" / dataset,
        repo_root / "backtester" / "datasets" / dataset,
    ]
    for c in candidates:
        if c.is_dir() and any(c.glob("prices_*.csv")):
            return c
    raise FileNotFoundError(f"No prices CSV found for dataset '{dataset}'")


def _stage_data_for_imcbt(src_dir: Path, round_num: int) -> Path:
    """imc-p4-bt expects --data <root>/round<N>/prices_round_<N>_day_<D>.csv."""
    tmp = Path(tempfile.mkdtemp(prefix=f"imcbt-data-r{round_num}-"))
    target = tmp / f"round{round_num}"
    target.mkdir(parents=True, exist_ok=True)
    for f in src_dir.glob("*.csv"):
        shutil.copy2(f, target / f.name)
    return tmp


def _parse_imcbt_log(log_path: Path):
    """Extract trades, activity, and per-product PnL from an imc-p4-bt .log file."""
    text = log_path.read_text(encoding="utf-8")

    # Trade history at the end of file: JSON array
    # Format: [{ "timestamp": ..., "buyer": ..., "seller": ..., "symbol": ..., "currency": ..., "price": ..., "quantity": ... }, ...]
    trade_re = re.compile(
        r'\{\s*"timestamp":\s*(\d+),\s*"buyer":\s*"([^"]*)",\s*'
        r'"seller":\s*"([^"]*)",\s*"symbol":\s*"([^"]+)",\s*'
        r'"currency":\s*"[^"]*",\s*"price":\s*([\-\d.]+),\s*'
        r'"quantity":\s*(\d+)'
    )
    trades = []
    for m in trade_re.finditer(text):
        ts, buyer, seller, sym, price, qty = m.groups()
        trades.append({
            "timestamp": int(ts),
            "buyer": buyer,
            "seller": seller,
            "symbol": sym,
            "price": float(price),
            "quantity": int(qty),
        })

    # Activity log lines from "Activities log:" section
    # Format: day;timestamp;product;bid_price_1;bid_volume_1;...mid_price;profit_and_loss
    activity_lines = []
    in_act = False
    for line in text.splitlines():
        if line.startswith("day;timestamp;product"):
            in_act = True
            activity_lines.append(line)
            continue
        if in_act:
            if not line or line.startswith("Trade History:") or line.startswith("Sandbox"):
                break
            if ";" in line:
                activity_lines.append(line)
            else:
                break

    return trades, activity_lines


def _aggregate_pnl_from_activity(activity_lines):
    """Build pnl_by_product.csv content from activity log's profit_and_loss column."""
    if len(activity_lines) < 2:
        return []
    header = activity_lines[0].split(";")
    pnl_idx = header.index("profit_and_loss")
    ts_idx = header.index("timestamp")
    prod_idx = header.index("product")
    rows_by_ts = {}
    products = set()
    for line in activity_lines[1:]:
        parts = line.split(";")
        if len(parts) <= pnl_idx: continue
        ts = int(parts[ts_idx])
        prod = parts[prod_idx]
        try:
            pnl = float(parts[pnl_idx])
        except ValueError:
            pnl = 0.0
        rows_by_ts.setdefault(ts, {})[prod] = pnl
        products.add(prod)
    products = sorted(products)
    out = []
    out.append(["timestamp", "total"] + products)
    for ts in sorted(rows_by_ts):
        row_pnl = rows_by_ts[ts]
        total = sum(row_pnl.values())
        out.append([ts, total] + [row_pnl.get(p, 0.0) for p in products])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trader", type=Path, required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--day", type=int, default=None)
    ap.add_argument("--queue-penetration", type=float, default=1.0)
    ap.add_argument("--book-fill", default="true")
    ap.add_argument("--price-slippage-bps", type=float, default=0.0)
    ap.add_argument("--persist", action="store_true")
    ap.add_argument("--artifact-mode", default="full")
    ap.add_argument("--output-root", type=Path, default=Path("runs"))
    ap.add_argument("--products", default="summary")
    ap.add_argument("--carry", action="store_true")
    ap.add_argument("--flat", action="store_true")
    ap.add_argument("--max-timestamp", type=int, default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--trade-match-mode", default="all")
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    src_data = _find_data_dir(repo_root, args.dataset)

    # Parse round number from dataset name (e.g. 'round4' → 4)
    m = re.match(r"round(\d+)", args.dataset)
    if not m:
        sys.exit(f"Cannot parse round number from dataset '{args.dataset}'")
    round_num = int(m.group(1))

    # Stage data into imc-p4-bt's expected layout
    data_root = _stage_data_for_imcbt(src_data, round_num)

    # Build day spec
    if args.day is not None:
        day_spec = f"{round_num}-{args.day}"
    else:
        day_spec = str(round_num)

    # Build limit overrides — Round 4 + Round 5 products.
    # All R5 products have a position limit of 10 per the round spec.
    LIMITS = {
        # Round 4
        "HYDROGEL_PACK": 200,
        "VELVETFRUIT_EXTRACT": 200,
        "VEV_4000": 300, "VEV_4500": 300, "VEV_5000": 300, "VEV_5100": 300,
        "VEV_5200": 300, "VEV_5300": 300, "VEV_5400": 300, "VEV_5500": 300,
        "VEV_6000": 300, "VEV_6500": 300,
    }
    R5_PRODUCTS = [
        "GALAXY_SOUNDS_DARK_MATTER","GALAXY_SOUNDS_BLACK_HOLES","GALAXY_SOUNDS_PLANETARY_RINGS",
        "GALAXY_SOUNDS_SOLAR_WINDS","GALAXY_SOUNDS_SOLAR_FLAMES",
        "SLEEP_POD_SUEDE","SLEEP_POD_LAMB_WOOL","SLEEP_POD_POLYESTER","SLEEP_POD_NYLON","SLEEP_POD_COTTON",
        "MICROCHIP_CIRCLE","MICROCHIP_OVAL","MICROCHIP_SQUARE","MICROCHIP_RECTANGLE","MICROCHIP_TRIANGLE",
        "PEBBLES_XS","PEBBLES_S","PEBBLES_M","PEBBLES_L","PEBBLES_XL",
        "ROBOT_VACUUMING","ROBOT_MOPPING","ROBOT_DISHES","ROBOT_LAUNDRY","ROBOT_IRONING",
        "UV_VISOR_YELLOW","UV_VISOR_AMBER","UV_VISOR_ORANGE","UV_VISOR_RED","UV_VISOR_MAGENTA",
        "TRANSLATOR_SPACE_GRAY","TRANSLATOR_ASTRO_BLACK","TRANSLATOR_ECLIPSE_CHARCOAL",
        "TRANSLATOR_GRAPHITE_MIST","TRANSLATOR_VOID_BLUE",
        "PANEL_1X2","PANEL_2X2","PANEL_1X4","PANEL_2X4","PANEL_4X4",
        "OXYGEN_SHAKE_MORNING_BREATH","OXYGEN_SHAKE_EVENING_BREATH","OXYGEN_SHAKE_MINT",
        "OXYGEN_SHAKE_CHOCOLATE","OXYGEN_SHAKE_GARLIC",
        "SNACKPACK_CHOCOLATE","SNACKPACK_VANILLA","SNACKPACK_PISTACHIO",
        "SNACKPACK_STRAWBERRY","SNACKPACK_RASPBERRY",
    ]
    for p in R5_PRODUCTS:
        LIMITS[p] = 10
    limit_args = []
    for sym, lim in LIMITS.items():
        limit_args += ["--limit", f"{sym}:{lim}"]

    # Output dir
    run_id = args.run_id or f"backtest-{int(time.time() * 1000)}"
    run_dir = args.output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    # Write the imc-p4-bt log into a temp dir, not the run dir, so the dashboard
    # only ships activity.csv / trades.csv / pnl_by_product.csv / metrics.json
    # to gh-pages. Full-day .log files are ~47MB each and were pushing the
    # Pages site over its 10GB limit; the dashboard never reads them anyway.
    tmp_log_dir = Path(tempfile.mkdtemp(prefix=f"imcbt-log-{run_id}-"))
    log_path = tmp_log_dir / "submission.log"

    # Run imc-p4-bt
    cmd = [
        "imc-p4-bt", str(args.trader), day_spec,
        "--data", str(data_root),
        "--out", str(log_path),
        "--no-progress",
        "--match-trades", "all" if args.trade_match_mode == "all" else "worse",
    ] + limit_args
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    print(f"=== imcbt_wrapper: running {' '.join(cmd[:6])} ... ===", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=240)
    if result.returncode != 0:
        sys.stderr.write(result.stderr[-1000:])
        sys.exit(f"imc-p4-bt failed: rc={result.returncode}")

    # Parse stdout for per-product PnL summary and total
    stdout = result.stdout
    final_pnl_total = 0.0
    final_pnl_by_product = {}
    for m in re.finditer(r"Total profit: ([\-\d,]+)", stdout):
        final_pnl_total = int(m.group(1).replace(",", ""))
    for m in re.finditer(r"^([A-Z_0-9]+):\s+([\-\d,]+)\s*$", stdout, re.M):
        sym, pnl = m.group(1), int(m.group(2).replace(",", ""))
        if sym in LIMITS:
            final_pnl_by_product[sym] = pnl

    # Parse log for trades + activity
    trades, activity_lines = _parse_imcbt_log(log_path)

    # Filter to SUBMISSION trades (own trades)
    own_trades = [t for t in trades
                  if t["buyer"] == "SUBMISSION" or t["seller"] == "SUBMISSION"]
    own_trade_count = len(own_trades)

    # Write trades.csv (semicolon-delimited to match IMC convention; the
    # SLU dashboard's fetchCsv expects ';' separator).
    with open(run_dir / "trades.csv", "w", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["timestamp", "buyer", "seller", "symbol", "currency", "price", "quantity"])
        for t in trades:
            w.writerow([t["timestamp"], t["buyer"], t["seller"], t["symbol"],
                        "XIREC", t["price"], t["quantity"]])

    # Write activity.csv (already ';'-delimited from imc-p4-bt log).
    with open(run_dir / "activity.csv", "w", newline="") as f:
        for line in activity_lines:
            f.write(line + "\n")

    # Build pnl_by_product.csv from activity log's PnL column
    pnl_rows = _aggregate_pnl_from_activity(activity_lines)
    with open(run_dir / "pnl_by_product.csv", "w", newline="") as f:
        w = csv.writer(f, delimiter=";")
        for row in pnl_rows:
            w.writerow(row)

    # Tick count from activity rows
    if pnl_rows:
        tick_count = len(pnl_rows) - 1   # subtract header
    else:
        tick_count = 0

    # Write metrics.json
    # dataset_path mirrors rust_backtester's convention (per-day prices CSV)
    # so the dashboard dedup key (author, strategy, dataset, day) cleanly
    # replaces older rust_backtester entries with the calibrated wrapper run.
    if args.day is not None:
        ds_csv = src_data / f"prices_round_{round_num}_day_{args.day}.csv"
    else:
        ds_csv = src_data
    metrics = {
        "dataset_path": str(ds_csv),
        "day": args.day if args.day is not None else "all",
        "final_pnl_total": final_pnl_total,
        "final_pnl_by_product": final_pnl_by_product,
        "own_trade_count": own_trade_count,
        "tick_count": tick_count,
        "max_drawdown_abs": 0.0,
        "sharpe_ratio": 0.0,
    }
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))

    # Copy the real imc-p4-bt log so the dashboard's "Open in visualizer"
    # link can fetch it. The deploy step in backtest.yml prunes to keep
    # real logs only for the N most recent runs per author; older runs
    # get stubbed there to bound gh-pages size.
    if log_path.exists():
        shutil.copy2(log_path, run_dir / "submission.log")
    else:
        (run_dir / "submission.log").write_text(
            f"# imcbt_wrapper: imc-p4-bt produced no log for run {run_id}\n"
        )

    # Print summary in rust_backtester format
    print(f"trader: {args.trader.name}")
    print(f"dataset: {args.dataset}")
    print(f"mode: imcbt-wrapper")
    print(f"artifacts: full")
    print(f"SET             DAY    TICKS  OWN_TRADES    FINAL_PNL  RUN_DIR")
    day_label = f"day-{args.day}" if args.day is not None else "all"
    print(f"{day_label:<15} {args.day if args.day is not None else '?':<6} {tick_count:>6} {own_trade_count:>11} {final_pnl_total:>12.2f}  runs/{run_id}")
    print()
    print(f"PRODUCT                  {day_label}")
    for sym, pnl in sorted(final_pnl_by_product.items(), key=lambda x: -abs(x[1])):
        print(f"{sym:<22} {pnl:>10.2f}")

    # Cleanup staged data + tmp log dir
    shutil.rmtree(data_root, ignore_errors=True)
    shutil.rmtree(tmp_log_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
