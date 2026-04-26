"""
Bid-ask spread plot for HYDROGEL_PACK and VELVETFRUIT_EXTRACT
across the configured days. Set ROUND/DAYS env vars to retarget.

Output: notebooks/round{ROUND}_spreads.png
"""

import csv
import os
from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).resolve().parent.parent
ROUND = int(os.environ.get("ROUND", "3"))
DAYS = [int(x) for x in os.environ.get("DAYS", "0,1,2").split(",")]
DATA = REPO / "backtester" / "datasets" / f"round{ROUND}"
OUT = REPO / "notebooks" / f"round{ROUND}_spreads.png"

PRODUCTS = ["HYDROGEL_PACK", "VELVETFRUIT_EXTRACT"]
TICKS_PER_DAY = 10_000


def load_spreads():
    spreads = {p: [] for p in PRODUCTS}
    for d in DAYS:
        with (DATA / f"prices_round_{ROUND}_day_{d}.csv").open() as f:
            for r in csv.DictReader(f, delimiter=";"):
                if r["product"] not in PRODUCTS:
                    continue
                if not r["bid_price_1"] or not r["ask_price_1"]:
                    spreads[r["product"]].append(np.nan)
                else:
                    spreads[r["product"]].append(int(r["ask_price_1"]) - int(r["bid_price_1"]))
    return spreads


def main():
    spreads = load_spreads()

    fig, axes = plt.subplots(2, 2, figsize=(14, 8),
                             gridspec_kw={"width_ratios": [3, 1]})
    fig.suptitle(
        f"Round {ROUND} — bid-ask spread over days {DAYS} (mean-reverting deltas)",
        fontsize=12,
    )

    for row, product in enumerate(PRODUCTS):
        s = np.asarray(spreads[product], dtype=float)
        x = np.arange(len(s))

        ax_ts = axes[row, 0]
        ax_hist = axes[row, 1]

        # time series — use thin line with low alpha
        ax_ts.plot(x, s, lw=0.3, color="#1f77b4", alpha=0.6)
        modal = Counter(int(v) for v in s if not np.isnan(v)).most_common(1)[0][0]
        mean = np.nanmean(s)
        ax_ts.axhline(modal, color="#d62728", ls="--", lw=0.7,
                      label=f"modal = {modal}")
        ax_ts.axhline(mean, color="#888", ls=":", lw=0.7,
                      label=f"mean = {mean:.2f}")
        ax_ts.set_ylabel("spread (price ticks)")
        ax_ts.set_title(f"{product}", fontsize=11)
        ax_ts.legend(loc="upper right", fontsize=8)
        ax_ts.grid(alpha=0.25)
        if row == 1:
            ax_ts.set_xlabel(f"tick index ({' → '.join(f'D{d}' for d in DAYS)})")

        # day boundaries + labels
        for k in range(1, len(DAYS)):
            ax_ts.axvline(k * TICKS_PER_DAY, color="black", lw=0.5, alpha=0.4)
        for i, d in enumerate(DAYS):
            ax_ts.text((i + 0.5) * TICKS_PER_DAY, ax_ts.get_ylim()[1],
                       f"D{d}", ha="center", va="top", fontsize=9, color="#555",
                       bbox=dict(facecolor="white", edgecolor="none", alpha=0.8, pad=1))

        # histogram on the right
        clean = s[~np.isnan(s)].astype(int)
        bins = np.arange(clean.min(), clean.max() + 2) - 0.5
        ax_hist.hist(clean, bins=bins, color="#1f77b4",
                     edgecolor="white", lw=0.4, orientation="horizontal")
        ax_hist.set_xlabel("count")
        ax_hist.set_title("distribution", fontsize=10)
        ax_hist.grid(alpha=0.25)
        # share y with the time-series
        ax_hist.set_ylim(ax_ts.get_ylim())

    plt.tight_layout(rect=(0, 0, 1, 0.96))
    plt.savefig(OUT, dpi=140, bbox_inches="tight")
    print(f"saved {OUT}")

    # text summary
    print("\nPer-product spread distribution:")
    for product in PRODUCTS:
        s = [int(v) for v in spreads[product] if not np.isnan(v)]
        c = Counter(s)
        print(f"\n  {product}  (n={len(s):,})")
        for w in sorted(c):
            print(f"    spread={w:>3}: {c[w]:>6} ({100 * c[w] / len(s):5.2f}%)")


if __name__ == "__main__":
    main()
