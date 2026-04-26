"""Extract per-tick activitiesLog + tradeHistory from a backtester
submission.log into two pandas-friendly CSVs.

Usage:
    python scripts/extract_run_csv.py <run_dir> [out_prefix]

Outputs:
    <out_prefix>_activity.csv   — timestamp, product, bid/ask/mid, pnl
    <out_prefix>_trades.csv     — own trades (timestamp, product, price, qty, side)
"""

import csv
import io
import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    run = Path(sys.argv[1])
    log = run / "submission.log"
    if not log.exists():
        sys.exit(f"no submission.log at {log}")
    out_prefix = Path(sys.argv[2]) if len(sys.argv) >= 3 else run / "extracted"

    payload = json.loads(log.read_text())
    activity = payload.get("activitiesLog", "")
    trades = payload.get("tradeHistory", [])

    # activitiesLog is a CSV string with header
    rows = list(csv.DictReader(io.StringIO(activity), delimiter=";"))
    keep = ["day", "timestamp", "product",
            "bid_price_1", "ask_price_1", "mid_price", "profit_and_loss"]
    out_act = Path(f"{out_prefix}_activity.csv")
    with out_act.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keep)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in keep})

    # tradeHistory is JSON list of {timestamp, symbol, price, quantity, buyer, seller}
    own = [t for t in trades if t.get("buyer") == "SUBMISSION" or t.get("seller") == "SUBMISSION"]
    out_tr = Path(f"{out_prefix}_trades.csv")
    with out_tr.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "symbol", "price", "quantity", "side", "counterparty"])
        for t in own:
            side = "buy" if t.get("buyer") == "SUBMISSION" else "sell"
            cp = t.get("seller") if side == "buy" else t.get("buyer")
            w.writerow([t["timestamp"], t["symbol"], t["price"], t["quantity"], side, cp])

    print(f"wrote {out_act}  ({len(rows)} rows)")
    print(f"wrote {out_tr}   ({len(own)} own trades)")


if __name__ == "__main__":
    main()
