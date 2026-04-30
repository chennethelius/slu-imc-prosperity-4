#!/usr/bin/env python3
"""Compute per-group product correlations and cointegration on Round 5 data.

For each of the 10 groups of 5 products, calculates:
  - pairwise correlation of mid returns (10 pairs/group)
  - rolling spread stability (std of pairwise mid difference)
  - cross-day correlation persistence

A high, persistent correlation (>0.7 across all 3 days) suggests the pair
is a candidate for spread mean-reversion trading — direction-agnostic
structural alpha.
"""
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
    """Load CSV, return {product: [(timestamp, mid), ...]}."""
    out = defaultdict(list)
    with open(path, newline="") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        idx = {c: i for i, c in enumerate(header)}
        for row in reader:
            if not row or len(row) < len(header):
                continue
            try:
                ts = int(row[idx["timestamp"]])
                prod = row[idx["product"]]
                mid_s = row[idx["mid_price"]]
                if not mid_s:
                    continue
                mid = float(mid_s)
                out[prod].append((ts, mid))
            except (ValueError, KeyError):
                continue
    return out


def correlate(seq_a, seq_b):
    """Pearson correlation of two equal-length sequences."""
    if len(seq_a) != len(seq_b) or len(seq_a) < 3:
        return None
    mu_a = sum(seq_a) / len(seq_a)
    mu_b = sum(seq_b) / len(seq_b)
    num = sum((a - mu_a) * (b - mu_b) for a, b in zip(seq_a, seq_b))
    den_a = sum((a - mu_a) ** 2 for a in seq_a) ** 0.5
    den_b = sum((b - mu_b) ** 2 for b in seq_b) ** 0.5
    if den_a == 0 or den_b == 0:
        return None
    return num / (den_a * den_b)


def diffs(seq):
    return [seq[i] - seq[i - 1] for i in range(1, len(seq))]


def main():
    repo = Path(__file__).resolve().parent.parent
    days = [2, 3, 4]
    data_root = repo / "datasets_extra" / "round5_data"

    print("Per-group pairwise correlations of mid-returns (across all 3 days)\n")
    print(f"{'group':<14} {'pair':<60} {'d2_corr':>8} {'d3_corr':>8} {'d4_corr':>8} {'min':>8}")

    all_results = []

    for group_name, products in GROUPS.items():
        # Load per-day mids for each product
        day_mids = {}  # day -> product -> [mids]
        for d in days:
            mids = load_mids(data_root / f"prices_round_5_day_{d}.csv")
            day_mids[d] = {p: [m for _, m in sorted(mids[p])] for p in products if p in mids}

        # All pairs in group
        for i, a in enumerate(products):
            for b in products[i+1:]:
                day_corrs = []
                for d in days:
                    seq_a = day_mids[d].get(a, [])
                    seq_b = day_mids[d].get(b, [])
                    n = min(len(seq_a), len(seq_b))
                    if n < 100:
                        day_corrs.append(None)
                        continue
                    # Use returns (diffs) not raw prices to filter integration
                    da = diffs(seq_a[:n])
                    db = diffs(seq_b[:n])
                    c = correlate(da, db)
                    day_corrs.append(c)
                pair_label = f"{a} vs {b}"
                # Format
                row_strs = [f"{c:>8.3f}" if c is not None else "    n/a " for c in day_corrs]
                valid = [c for c in day_corrs if c is not None]
                min_corr = min(valid) if valid else None
                min_str = f"{min_corr:>8.3f}" if min_corr is not None else "    n/a "
                print(f"{group_name:<14} {pair_label:<60} {' '.join(row_strs)} {min_str}")
                if min_corr is not None and min_corr > 0.4:
                    all_results.append((group_name, a, b, day_corrs, min_corr))

    print("\n=== Strongest persistent correlations (min day-corr > 0.4) ===")
    all_results.sort(key=lambda r: -r[4])
    for group, a, b, corrs, mn in all_results[:25]:
        c_strs = " ".join(f"{c:.2f}" if c else "n/a" for c in corrs)
        print(f"  {group:<14} {a:<32} ~ {b:<32} corrs=[{c_strs}] min={mn:.3f}")


if __name__ == "__main__":
    main()
