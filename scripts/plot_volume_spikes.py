"""
Plot mid prices for HYDROGEL_PACK and VELVETFRUIT_EXTRACT across the
configured days, with markers at ticks where the per-tick traded volume
is ≥ MIN_VOL. Set ROUND/DAYS env vars to retarget.

Output: notebooks/round{ROUND}_volume_spikes.png
"""

import csv
import os
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parent.parent
ROUND = int(os.environ.get("ROUND", "3"))
DAYS = [int(x) for x in os.environ.get("DAYS", "0,1,2").split(",")]
DATA = REPO / "backtester" / "datasets" / f"round{ROUND}"
OUT = REPO / "notebooks" / f"round{ROUND}_volume_spikes.png"

PRODUCTS = ["HYDROGEL_PACK", "VELVETFRUIT_EXTRACT"]
# Per-product cutoffs (~top 5% per-tick volume). HYDROGEL caps at 6/tick,
# VELVETFRUIT goes to 19/tick.
MIN_VOL = {"HYDROGEL_PACK": 6, "VELVETFRUIT_EXTRACT": 10}
TICKS_PER_DAY = 10_000


def load_mids():
    """Returns dict[product] -> list[(global_idx, mid)]."""
    out = {p: [] for p in PRODUCTS}
    for slot, d in enumerate(DAYS):
        with (DATA / f"prices_round_{ROUND}_day_{d}.csv").open() as f:
            for r in csv.DictReader(f, delimiter=";"):
                if r["product"] not in out or not r["mid_price"]:
                    continue
                ts = int(r["timestamp"])
                gi = slot * TICKS_PER_DAY + ts // 100
                out[r["product"]].append((gi, float(r["mid_price"])))
    return out


def load_trade_vol():
    """Returns dict[product] -> dict[global_idx] -> total qty traded at that tick."""
    out = {p: defaultdict(int) for p in PRODUCTS}
    for slot, d in enumerate(DAYS):
        with (DATA / f"trades_round_{ROUND}_day_{d}.csv").open() as f:
            for r in csv.DictReader(f, delimiter=";"):
                if r["symbol"] not in out:
                    continue
                ts = int(r["timestamp"])
                gi = slot * TICKS_PER_DAY + ts // 100
                out[r["symbol"]][gi] += int(r["quantity"])
    return out


def main():
    mids = load_mids()
    vols = load_trade_vol()

    fig, axes = plt.subplots(len(PRODUCTS), 1, figsize=(13, 4 * len(PRODUCTS)),
                             sharex=True)
    for ax, prod in zip(axes, PRODUCTS):
        series = mids[prod]
        if not series:
            continue
        gx = [g for g, _ in series]
        gy = [m for _, m in series]
        ax.plot(gx, gy, lw=0.6, color="steelblue", alpha=0.85)

        # Overlay markers where per-tick volume ≥ MIN_VOL[prod]
        thr = MIN_VOL[prod]
        mid_at = dict(series)
        spike_x, spike_y, spike_v = [], [], []
        for gi, vol in vols[prod].items():
            if vol >= thr and gi in mid_at:
                spike_x.append(gi)
                spike_y.append(mid_at[gi])
                spike_v.append(vol)
        if spike_x:
            sc = ax.scatter(spike_x, spike_y, s=[v * 4 for v in spike_v],
                            c=spike_v, cmap="plasma", alpha=0.7,
                            edgecolors="black", linewidths=0.3, zorder=3)
            cbar = fig.colorbar(sc, ax=ax, pad=0.01)
            cbar.set_label(f"trade qty (≥{thr})", fontsize=8)
        ax.set_ylabel(f"{prod}\nmid price")
        ax.set_title(f"{prod}: mid + trade-volume spikes (qty ≥ {thr}); "
                     f"{len(spike_x)} spikes",
                     fontsize=10)
        ax.grid(alpha=0.25)
        for k in range(1, len(DAYS)):
            ax.axvline(k * TICKS_PER_DAY, color="black", lw=0.4, alpha=0.4)

        # Print per-day spike summary
        per_slot = defaultdict(int)
        for gi in spike_x:
            per_slot[gi // TICKS_PER_DAY] += 1
        per_day_msg = "  ".join(f"D{d}={per_slot[s]}" for s, d in enumerate(DAYS))
        print(f"{prod}:  total spikes={len(spike_x)}  per-day: {per_day_msg}  "
              f"max qty={max(spike_v) if spike_v else 0}")

    axes[-1].set_xlabel(f"global tick ({' → '.join(f'D{d}' for d in DAYS)})")
    fig.suptitle(f"Mid + volume spikes (per-tick traded qty ≥ {MIN_VOL})",
                 fontsize=12)
    plt.tight_layout()
    plt.savefig(OUT, dpi=140, bbox_inches="tight")
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()
