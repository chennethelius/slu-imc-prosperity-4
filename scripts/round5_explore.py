"""Round 5 data exploration: per-product stats, intra-group correlations,
counterparty distribution, spread/depth profiles."""
import csv, math, statistics
from collections import defaultdict
from pathlib import Path

DATA = Path("C:/Users/thisi/OneDrive/Desktop/IMC Prosperity 4/data/ROUND_5")

GROUPS = {
    "GalaxySounds":   ["GALAXY_SOUNDS_DARK_MATTER","GALAXY_SOUNDS_BLACK_HOLES","GALAXY_SOUNDS_PLANETARY_RINGS","GALAXY_SOUNDS_SOLAR_WINDS","GALAXY_SOUNDS_SOLAR_FLAMES"],
    "SleepPods":      ["SLEEP_POD_SUEDE","SLEEP_POD_LAMB_WOOL","SLEEP_POD_POLYESTER","SLEEP_POD_NYLON","SLEEP_POD_COTTON"],
    "Microchips":     ["MICROCHIP_CIRCLE","MICROCHIP_OVAL","MICROCHIP_SQUARE","MICROCHIP_RECTANGLE","MICROCHIP_TRIANGLE"],
    "Pebbles":        ["PEBBLES_XS","PEBBLES_S","PEBBLES_M","PEBBLES_L","PEBBLES_XL"],
    "Robots":         ["ROBOT_VACUUMING","ROBOT_MOPPING","ROBOT_DISHES","ROBOT_LAUNDRY","ROBOT_IRONING"],
    "UVVisors":       ["UV_VISOR_YELLOW","UV_VISOR_AMBER","UV_VISOR_ORANGE","UV_VISOR_RED","UV_VISOR_MAGENTA"],
    "Translators":    ["TRANSLATOR_SPACE_GRAY","TRANSLATOR_ASTRO_BLACK","TRANSLATOR_ECLIPSE_CHARCOAL","TRANSLATOR_GRAPHITE_MIST","TRANSLATOR_VOID_BLUE"],
    "Panels":         ["PANEL_1X2","PANEL_2X2","PANEL_1X4","PANEL_2X4","PANEL_4X4"],
    "OxygenShakes":   ["OXYGEN_SHAKE_MORNING_BREATH","OXYGEN_SHAKE_EVENING_BREATH","OXYGEN_SHAKE_MINT","OXYGEN_SHAKE_CHOCOLATE","OXYGEN_SHAKE_GARLIC"],
    "Snackpacks":     ["SNACKPACK_CHOCOLATE","SNACKPACK_VANILLA","SNACKPACK_PISTACHIO","SNACKPACK_STRAWBERRY","SNACKPACK_RASPBERRY"],
}
PROD_TO_GROUP = {p: g for g, ps in GROUPS.items() for p in ps}


def load_prices_day(day: int):
    """Returns: by_prod[product] = list of (ts, mid, bb, ba, bv1, av1, spread)."""
    out = defaultdict(list)
    fn = DATA / f"prices_round_5_day_{day}.csv"
    with open(fn, encoding="utf-8") as f:
        r = csv.reader(f, delimiter=";")
        header = next(r)
        idx = {h: i for i, h in enumerate(header)}
        for row in r:
            try:
                ts = int(row[idx["timestamp"]])
                prod = row[idx["product"]]
                mid = float(row[idx["mid_price"]]) if row[idx["mid_price"]] else None
                bb = float(row[idx["bid_price_1"]]) if row[idx["bid_price_1"]] else None
                ba = float(row[idx["ask_price_1"]]) if row[idx["ask_price_1"]] else None
                bv = float(row[idx["bid_volume_1"]]) if row[idx["bid_volume_1"]] else 0
                av = float(row[idx["ask_volume_1"]]) if row[idx["ask_volume_1"]] else 0
                if mid is None or bb is None or ba is None:
                    continue
                spread = ba - bb
                out[prod].append((ts, mid, bb, ba, bv, av, spread))
            except (ValueError, IndexError):
                pass
    return out


def load_trades_day(day: int):
    out = []
    fn = DATA / f"trades_round_5_day_{day}.csv"
    with open(fn, encoding="utf-8") as f:
        r = csv.reader(f, delimiter=";")
        header = next(r)
        idx = {h: i for i, h in enumerate(header)}
        for row in r:
            try:
                out.append({
                    "ts": int(row[idx["timestamp"]]),
                    "buyer": row[idx["buyer"]],
                    "seller": row[idx["seller"]],
                    "symbol": row[idx["symbol"]],
                    "price": float(row[idx["price"]]),
                    "qty": int(row[idx["quantity"]]),
                })
            except (ValueError, IndexError):
                pass
    return out


def main():
    print("=== Per-product stats (mid, std, spread) — averaged across days ===")
    print(f"{'product':<35} {'group':<14} {'mean':>10} {'std':>8} {'avg_spread':>10} {'n_ticks':>8}")
    all_data = {}
    for day in (2, 3, 4):
        all_data[day] = load_prices_day(day)

    # Per-product across-day stats
    by_prod = defaultdict(list)
    for day, data in all_data.items():
        for prod, rows in data.items():
            for r in rows:
                by_prod[prod].append((day, r))

    stats = {}
    for prod, entries in by_prod.items():
        mids = [r[1][1] for r in entries]
        spreads = [r[1][6] for r in entries]
        mean = sum(mids) / len(mids)
        std = statistics.pstdev(mids)
        avg_spread = sum(spreads) / len(spreads)
        stats[prod] = (mean, std, avg_spread, len(mids))

    # Print sorted by group
    for g, prods in GROUPS.items():
        print(f"--- {g} ---")
        for prod in prods:
            if prod in stats:
                mean, std, sp, n = stats[prod]
                print(f"  {prod:<35} {g:<14} {mean:>10.2f} {std:>8.2f} {sp:>10.2f} {n:>8}")
            else:
                print(f"  {prod:<35} {g:<14} (no data)")

    # Counterparty analysis: who trades each group?
    print("\n=== Counterparty distribution per product (3-day total trade counts) ===")
    trade_counts = defaultdict(lambda: defaultdict(int))
    for day in (2, 3, 4):
        trades = load_trades_day(day)
        for t in trades:
            for who in (t["buyer"], t["seller"]):
                if who and who.startswith("Mark"):
                    trade_counts[t["symbol"]][who] += t["qty"]
    for g, prods in GROUPS.items():
        print(f"--- {g} ---")
        for prod in prods:
            counts = trade_counts.get(prod, {})
            top = sorted(counts.items(), key=lambda x: -x[1])[:3]
            top_str = "  ".join(f"{m}={n}" for m, n in top) if top else "(no marks)"
            print(f"  {prod:<35}  {top_str}")

    # Intra-group correlation: align mids by ts and compute pairwise correlation
    print("\n=== Intra-group correlation (D2 only, lag=0) ===")
    d2 = all_data[2]
    for g, prods in GROUPS.items():
        # Build ts → mid per product
        mid_by_ts = {}
        for p in prods:
            mid_by_ts[p] = {r[0]: r[1] for r in d2.get(p, [])}
        common_ts = set.intersection(*[set(d.keys()) for d in mid_by_ts.values()]) if all(len(d)>0 for d in mid_by_ts.values()) else set()
        if not common_ts:
            print(f"--- {g} --- (no common ts)")
            continue
        common_ts = sorted(common_ts)
        # Compute correlation between each pair on log-returns
        rets = {}
        for p in prods:
            mids = [mid_by_ts[p][t] for t in common_ts]
            r = [math.log(mids[i+1]/mids[i]) if mids[i]>0 and mids[i+1]>0 else 0 for i in range(len(mids)-1)]
            rets[p] = r
        n = len(common_ts) - 1
        means = {p: sum(rets[p])/n for p in prods}
        print(f"--- {g} (n_ticks={n}) ---")
        print("       " + "    ".join(p.split('_')[-1][:6].rjust(6) for p in prods))
        for p1 in prods:
            cells = [p1.split('_')[-1][:6].rjust(6)]
            for p2 in prods:
                if p1 == p2:
                    cells.append("   1.00")
                    continue
                num = sum((rets[p1][i]-means[p1])*(rets[p2][i]-means[p2]) for i in range(n))
                v1 = math.sqrt(sum((r-means[p1])**2 for r in rets[p1]))
                v2 = math.sqrt(sum((r-means[p2])**2 for r in rets[p2]))
                if v1*v2 == 0:
                    cells.append("   0.00")
                else:
                    cells.append(f"  {num/(v1*v2):+.2f}")
            print("  " + " ".join(cells))


if __name__ == "__main__":
    main()
