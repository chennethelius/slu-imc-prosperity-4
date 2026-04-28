"""
Build .github/round5_data.json — per-product mid/spread/volume series and
trade events for the interactive round5.html page.

Round 5 has 50 products in 10 groups of 5. Counterparty fields in trade CSVs
are empty (unlike round 4), so the page focuses on price/spread/volume and
within-group overlays for stat-arb spotting.

Structure:
  {
    "groups": {"PEBBLES": ["XS","S","M","L","XL"], ...},
    "days": [2, 3, 4],
    "products": {
      "PEBBLES_L": {
        "2": {                            # day 2
          "mids":    [[ts, price], ...],     # decimated to MAX_POINTS
          "spreads": [[ts, ask-bid], ...],
          "vols":    [[ts, bid_v1+ask_v1], ...],
          "trades":  [[ts, price, qty], ...]  # all trades, no counterparty
        },
        ...
      },
      ...
    }
  }
"""
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
DATASET_DIR = REPO / "backtester" / "datasets" / "round5"
DAYS = (2, 3, 4)
MAX_POINTS = 1000  # per (product, day) for time series
OUT = REPO / ".github" / "round5_data.json"

GROUPS = {
    "GALAXY_SOUNDS": ["BLACK_HOLES", "DARK_MATTER", "PLANETARY_RINGS", "SOLAR_FLAMES", "SOLAR_WINDS"],
    "MICROCHIP":     ["CIRCLE", "OVAL", "RECTANGLE", "SQUARE", "TRIANGLE"],
    "OXYGEN_SHAKE":  ["CHOCOLATE", "EVENING_BREATH", "GARLIC", "MINT", "MORNING_BREATH"],
    "PANEL":         ["1X2", "1X4", "2X2", "2X4", "4X4"],
    "PEBBLES":       ["XS", "S", "M", "L", "XL"],
    "ROBOT":         ["DISHES", "IRONING", "LAUNDRY", "MOPPING", "VACUUMING"],
    "SLEEP_POD":     ["COTTON", "LAMB_WOOL", "NYLON", "POLYESTER", "SUEDE"],
    "SNACKPACK":     ["CHOCOLATE", "PISTACHIO", "RASPBERRY", "STRAWBERRY", "VANILLA"],
    "TRANSLATOR":    ["ASTRO_BLACK", "ECLIPSE_CHARCOAL", "GRAPHITE_MIST", "SPACE_GRAY", "VOID_BLUE"],
    "UV_VISOR":      ["AMBER", "MAGENTA", "ORANGE", "RED", "YELLOW"],
}


def decimate(rows: list, n: int) -> list:
    if len(rows) <= n:
        return rows
    stride = max(1, len(rows) // n)
    out = rows[::stride]
    if rows[-1] is not out[-1]:
        out.append(rows[-1])
    return out


def main() -> None:
    products: dict[str, dict] = defaultdict(dict)

    for d in DAYS:
        prices = pd.read_csv(DATASET_DIR / f"prices_round_5_day_{d}.csv", sep=";")
        trades = pd.read_csv(DATASET_DIR / f"trades_round_5_day_{d}.csv", sep=";")

        for sym, psub in prices.groupby("product"):
            psub = psub.dropna(subset=["mid_price"]).sort_values("timestamp")
            if psub.empty:
                continue

            ts = psub["timestamp"].astype(int).tolist()
            mid = psub["mid_price"].astype(float).tolist()
            bid1 = psub["bid_price_1"].fillna(0).astype(float).tolist()
            ask1 = psub["ask_price_1"].fillna(0).astype(float).tolist()
            bv1 = psub["bid_volume_1"].fillna(0).astype(int).tolist()
            av1 = psub["ask_volume_1"].fillna(0).astype(int).tolist()

            mids = [[ts[i], mid[i]] for i in range(len(ts))]
            spreads = [
                [ts[i], round(ask1[i] - bid1[i], 2) if (bid1[i] and ask1[i]) else 0]
                for i in range(len(ts))
            ]
            vols = [[ts[i], bv1[i] + av1[i]] for i in range(len(ts))]

            mids = decimate(mids, MAX_POINTS)
            spreads = decimate(spreads, MAX_POINTS)
            vols = decimate(vols, MAX_POINTS)

            tsub = trades[trades["symbol"] == sym]
            trade_rows = [
                [int(r.timestamp), float(r.price), int(r.quantity)]
                for r in tsub.itertuples(index=False)
            ]

            products[sym][str(d)] = {
                "mids": mids,
                "spreads": spreads,
                "vols": vols,
                "trades": trade_rows,
            }

    payload = {
        "groups": GROUPS,
        "days": list(DAYS),
        "products": products,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":")))
    size_kb = OUT.stat().st_size / 1024
    n_prods = len(products)
    n_trades = sum(len(p[str(d)]["trades"]) for p in products.values() for d in DAYS if str(d) in p)
    print(f"Wrote {OUT.relative_to(REPO)}  ({size_kb:.0f} KB, {n_prods} products, {n_trades} trades)")


if __name__ == "__main__":
    main()
