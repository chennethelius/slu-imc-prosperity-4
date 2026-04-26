"""
Bid-ask spread plot for HYDROGEL_PACK and VELVETFRUIT_EXTRACT
across all 3 days of round 3.

Output: notebooks/round3_spreads.png
"""

import csv
from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "backtester" / "datasets" / "round3"
OUT = REPO / "notebooks" / "round3_spreads.png"

PRODUCTS = ["HYDROGEL_PACK", "VELVETFRUIT_EXTRACT"]
TICKS_PER_DAY = 10_000


def load_spreads():
    spreads = {p: [] for p in PRODUCTS}
    for d in (0, 1, 2):
        with (DATA / f"prices_round_3_day_{d}.csv").open() as f:
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
        "Round 3 — bid-ask spread over 3 days (mean-reverting deltas)",
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
            ax_ts.set_xlabel("tick index (D0 → D1 → D2)")

        # day boundaries + labels
        for d_end in (TICKS_PER_DAY, 2 * TICKS_PER_DAY):
            ax_ts.axvline(d_end, color="black", lw=0.5, alpha=0.4)
        for i, label in enumerate(("D0", "D1", "D2")):
            ax_ts.text((i + 0.5) * TICKS_PER_DAY, ax_ts.get_ylim()[1],
                       label, ha="center", va="top", fontsize=9, color="#555",
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
