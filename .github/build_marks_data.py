"""
Build .github/marks_data.json — per-day, per-product mid-price series and
Mark counterparty trade events for the interactive marks.html page.

Structure:
  {
    "marks": ["Mark 01", "Mark 14", ...],
    "days": [
      {
        "key": "round4_d1",
        "label": "Round 4 — day 1",
        "products": {
          "HYDROGEL_PACK": {
            "mids":   [[ts, price], ...],     # decimated to ≤600 points
            "trades": [{ts, p, qty, b, s}, ...]
          },
          ...
        }
      },
      ...
    ]
  }
"""
import json
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
DAYS = [
    ("round3", 0, "Round 3 — day 0 (== r4 d0 baseline)"),
    ("round4", 1, "Round 4 — day 1"),
    ("round4", 2, "Round 4 — day 2"),
    ("round4", 3, "Round 4 — day 3"),
]
PRODUCTS = [
    "HYDROGEL_PACK", "VELVETFRUIT_EXTRACT",
    "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100",
    "VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500",
]
MID_MAX_POINTS = 600  # decimate per (day, product)

OUT = REPO / ".github" / "marks_data.json"


def decimate(rows: list[list[float]], n: int) -> list[list[float]]:
    if len(rows) <= n:
        return rows
    stride = max(1, len(rows) // n)
    out = rows[::stride]
    if rows[-1] is not out[-1]:
        out.append(rows[-1])
    return out


def main() -> None:
    days_out = []
    marks: set[str] = set()

    for ds, d, label in DAYS:
        prefix = f"{ds[:5]}_{ds[5:]}_day_{d}"
        prices = pd.read_csv(REPO / f"backtester/datasets/{ds}/prices_{prefix}.csv", sep=";")
        trades = pd.read_csv(REPO / f"backtester/datasets/{ds}/trades_{prefix}.csv", sep=";")

        prods_out = {}
        for sym in PRODUCTS:
            psub = prices[prices["product"] == sym].dropna(subset=["mid_price"]).sort_values("timestamp")
            if psub.empty:
                continue
            mids = list(zip(psub["timestamp"].astype(int), psub["mid_price"].astype(float)))
            mids = decimate([list(m) for m in mids], MID_MAX_POINTS)

            tsub = trades[trades["symbol"] == sym].copy()
            if not tsub.empty:
                marks.update(tsub["buyer"].dropna().unique())
                marks.update(tsub["seller"].dropna().unique())
            trades_out = [
                {"ts": int(r.timestamp), "p": float(r.price),
                 "qty": int(r.quantity), "b": r.buyer, "s": r.seller}
                for r in tsub.itertuples(index=False)
            ]

            prods_out[sym] = {"mids": mids, "trades": trades_out}

        days_out.append({"key": f"{ds}_d{d}", "label": label, "products": prods_out})

    # Restrict to mark traders, sorted
    mark_list = sorted([m for m in marks if isinstance(m, str) and m.startswith("Mark")])

    payload = {"marks": mark_list, "days": days_out}
    OUT.write_text(json.dumps(payload, separators=(",", ":")))
    size_kb = OUT.stat().st_size / 1024
    print(f"Wrote {OUT.relative_to(REPO)}  ({size_kb:.0f} KB, {len(mark_list)} marks, {len(days_out)} days)")


if __name__ == "__main__":
    main()
