"""
Verify that IMC's `mid_price` column is exactly (best_bid + best_ask) / 2.

Two modes:
  1. Default — checks every prices CSV under backtester/datasets/
     (historical replays).
  2. With a path to a submission .log JSON — checks the activitiesLog
     embedded in IMC's official tester output (live environment).

    python scripts/verify_mid_formula.py
    python scripts/verify_mid_formula.py round3
    python scripts/verify_mid_formula.py 425938/425938.log
"""

import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASETS_ROOT = REPO_ROOT / "backtester" / "datasets"


def verify_rows(rows) -> tuple[int, int, list[dict]]:
    """Returns (rows_checked, rows_skipped, mismatches)."""
    checked = skipped = 0
    mismatches: list[dict] = []
    for row in rows:
        bid = row.get("bid_price_1") or ""
        ask = row.get("ask_price_1") or ""
        reported = row.get("mid_price") or ""
        if not bid or not ask or not reported:
            skipped += 1
            continue
        computed = (float(bid) + float(ask)) / 2
        if abs(computed - float(reported)) > 1e-9:
            mismatches.append(
                {
                    "ts": row["timestamp"],
                    "product": row["product"],
                    "bid": bid,
                    "ask": ask,
                    "computed": computed,
                    "reported": reported,
                }
            )
        checked += 1
    return checked, skipped, mismatches


def verify(csv_path: Path) -> tuple[int, int, list[dict]]:
    with csv_path.open() as f:
        return verify_rows(csv.DictReader(f, delimiter=";"))


def verify_submission_log(log_path: Path) -> tuple[int, int, list[dict]]:
    """Verify against the activitiesLog inside an IMC submission .log file."""
    with log_path.open() as f:
        blob = json.load(f)
    csv_text = blob["activitiesLog"]
    reader = csv.DictReader(csv_text.splitlines(), delimiter=";")
    return verify_rows(reader)


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else None

    if target and target.endswith(".log"):
        log_path = Path(target)
        if not log_path.is_absolute():
            log_path = REPO_ROOT / log_path
        checked, skipped, mismatches = verify_submission_log(log_path)
        print(f"  source: IMC submission log {log_path.relative_to(REPO_ROOT)}")
        print(f"  checked={checked} skipped={skipped} mismatches={len(mismatches)}")
        for m in mismatches[:10]:
            print(f"    ts={m['ts']} {m['product']} bid={m['bid']} ask={m['ask']} "
                  f"computed={m['computed']} reported={m['reported']}")
        if not mismatches:
            print("CONFIRMED on IMC official tester output: "
                  "mid_price = (bid_price_1 + ask_price_1) / 2")
        else:
            print(f"REJECTED: {len(mismatches)} live-tester rows do NOT match.")
        sys.exit(0 if not mismatches else 1)

    csv_paths = sorted(DATASETS_ROOT.glob("**/prices_*.csv"))
    if target:
        csv_paths = [p for p in csv_paths if target in str(p)]
    if not csv_paths:
        print(f"No prices CSVs found under {DATASETS_ROOT}")
        sys.exit(1)

    total_checked = total_skipped = total_mismatch = 0
    for path in csv_paths:
        checked, skipped, mismatches = verify(path)
        total_checked += checked
        total_skipped += skipped
        total_mismatch += len(mismatches)
        rel = path.relative_to(REPO_ROOT)
        flag = "OK" if not mismatches else f"FAIL ({len(mismatches)})"
        print(f"  {flag:>10}  {rel}  checked={checked} skipped={skipped}")
        for m in mismatches[:5]:
            print(f"        ts={m['ts']} {m['product']} bid={m['bid']} ask={m['ask']} "
                  f"computed={m['computed']} reported={m['reported']}")
        if len(mismatches) > 5:
            print(f"        ... and {len(mismatches) - 5} more")

    print(f"\nTotal: checked={total_checked} skipped={total_skipped} "
          f"mismatches={total_mismatch}")
    if total_mismatch == 0:
        print("CONFIRMED: mid_price = (bid_price_1 + ask_price_1) / 2 across every row.")
    else:
        print(f"REJECTED: {total_mismatch} rows do NOT match the simple midpoint.")
    sys.exit(0 if total_mismatch == 0 else 1)


if __name__ == "__main__":
    main()
