#!/usr/bin/env python3
"""Parse IMC submission log: per-product final PnL and per-day mid drift."""
import json
import sys
from collections import defaultdict
from pathlib import Path


def parse(log_path: Path):
    raw = log_path.read_text(encoding="utf-8")
    obj = json.loads(raw)
    csv = obj["activitiesLog"]
    lines = csv.splitlines()
    header = lines[0].split(";")
    idx = {c: i for i, c in enumerate(header)}

    last_pnl = defaultdict(float)
    first_mid = {}
    last_mid = {}
    first_mid_day = {}
    last_mid_day = {}

    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split(";")
        if len(parts) < len(header):
            continue
        try:
            day = int(parts[idx["day"]])
            ts = int(parts[idx["timestamp"]])
            prod = parts[idx["product"]]
            mid = float(parts[idx["mid_price"]]) if parts[idx["mid_price"]] else None
            pnl = float(parts[idx["profit_and_loss"]]) if parts[idx["profit_and_loss"]] else 0.0
        except (ValueError, KeyError):
            continue
        last_pnl[prod] = pnl  # last row per product carries cumulative PnL
        if mid is not None:
            key = (prod, day)
            if key not in first_mid_day:
                first_mid_day[key] = mid
            last_mid_day[key] = mid
            if prod not in first_mid:
                first_mid[prod] = mid
            last_mid[prod] = mid

    return last_pnl, first_mid, last_mid, first_mid_day, last_mid_day


def main():
    log = Path(sys.argv[1])
    last_pnl, first_mid, last_mid, _, _ = parse(log)
    rows = []
    for prod in sorted(last_pnl):
        pnl = last_pnl[prod]
        drift = (last_mid.get(prod, 0) - first_mid.get(prod, 0))
        rows.append((prod, pnl, drift))
    rows.sort(key=lambda r: -r[1])
    print(f"{'product':<32} {'pnl':>10} {'drift':>10}")
    total = 0.0
    for prod, pnl, drift in rows:
        print(f"{prod:<32} {pnl:>10.1f} {drift:>10.1f}")
        total += pnl
    print(f"{'TOTAL':<32} {total:>10.1f}")


if __name__ == "__main__":
    main()
