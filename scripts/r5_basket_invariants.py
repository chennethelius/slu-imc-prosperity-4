#!/usr/bin/env python3
"""Check if each group has a sum-invariant constant across days."""
import csv
from collections import defaultdict
from pathlib import Path
import statistics


GROUPS = {
    "PEBBLES":       ["PEBBLES_XS", "PEBBLES_S", "PEBBLES_M", "PEBBLES_L", "PEBBLES_XL"],
    "PANEL":         ["PANEL_1X2", "PANEL_1X4", "PANEL_2X2", "PANEL_2X4", "PANEL_4X4"],
    "ROBOT":         ["ROBOT_DISHES", "ROBOT_IRONING", "ROBOT_LAUNDRY", "ROBOT_MOPPING", "ROBOT_VACUUMING"],
    "MICROCHIP":     ["MICROCHIP_CIRCLE", "MICROCHIP_OVAL", "MICROCHIP_RECTANGLE", "MICROCHIP_SQUARE", "MICROCHIP_TRIANGLE"],
    "TRANSLATOR":    ["TRANSLATOR_ASTRO_BLACK", "TRANSLATOR_ECLIPSE_CHARCOAL", "TRANSLATOR_GRAPHITE_MIST", "TRANSLATOR_SPACE_GRAY", "TRANSLATOR_VOID_BLUE"],
    "UV_VISOR":      ["UV_VISOR_RED", "UV_VISOR_ORANGE", "UV_VISOR_YELLOW", "UV_VISOR_AMBER", "UV_VISOR_MAGENTA"],
    "SLEEP_POD":     ["SLEEP_POD_COTTON", "SLEEP_POD_LAMB_WOOL", "SLEEP_POD_NYLON", "SLEEP_POD_POLYESTER", "SLEEP_POD_SUEDE"],
    "OXYGEN_SHAKE":  ["OXYGEN_SHAKE_CHOCOLATE", "OXYGEN_SHAKE_EVENING_BREATH", "OXYGEN_SHAKE_GARLIC", "OXYGEN_SHAKE_MINT", "OXYGEN_SHAKE_MORNING_BREATH"],
    "SNACKPACK":     ["SNACKPACK_CHOCOLATE", "SNACKPACK_PISTACHIO", "SNACKPACK_RASPBERRY", "SNACKPACK_STRAWBERRY", "SNACKPACK_VANILLA"],
    "GALAXY_SOUNDS": ["GALAXY_SOUNDS_BLACK_HOLES", "GALAXY_SOUNDS_DARK_MATTER", "GALAXY_SOUNDS_PLANETARY_RINGS", "GALAXY_SOUNDS_SOLAR_FLAMES", "GALAXY_SOUNDS_SOLAR_WINDS"],
}


def load_mids(path):
    out = defaultdict(list)
    with open(path, newline="") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        idx = {c: i for i, c in enumerate(header)}
        for row in reader:
            if not row or len(row) < len(header): continue
            try:
                ts = int(row[idx["timestamp"]])
                prod = row[idx["product"]]
                mid_s = row[idx["mid_price"]]
                if not mid_s: continue
                out[prod].append((ts, float(mid_s)))
            except (ValueError, KeyError):
                continue
    return out


def main():
    repo = Path(__file__).resolve().parent.parent
    data_root = repo / "datasets_extra" / "round5_data"
    days = [2, 3, 4]

    print(f"\n{'group':<14} {'day':>4} {'sum_mean':>12} {'sum_sd':>10} {'max_dev':>10} {'n_2sd':>8}")
    for group, products in GROUPS.items():
        for d in days:
            mids = load_mids(data_root / f"prices_round_5_day_{d}.csv")
            seqs = {}
            for p in products:
                ms = sorted(mids[p])
                seqs[p] = [m for _, m in ms]
            n = min((len(s) for s in seqs.values()), default=0)
            if n < 100:
                continue
            sums = [sum(seqs[p][i] for p in products) for i in range(n)]
            mu = statistics.mean(sums)
            sd = statistics.stdev(sums)
            max_dev = max(abs(s - mu) for s in sums)
            n_above = sum(1 for s in sums if abs(s - mu) > 2 * sd)
            print(f"{group:<14} {d:>4} {mu:>12,.1f} {sd:>10.2f} {max_dev:>10.1f} {n_above:>8}")


if __name__ == "__main__":
    main()
