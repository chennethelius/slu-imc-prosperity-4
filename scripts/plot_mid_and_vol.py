"""
Mid price + rolling-volatility plot for HYDROGEL_PACK and VELVETFRUIT_EXTRACT
across all 3 days of round 3.

Volatility = rolling stdev of 1-tick mid changes over a 100-tick window.

Output: notebooks/round3_mid_vol.png
"""

import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "backtester" / "datasets" / "round3"
OUT = REPO / "notebooks" / "round3_mid_vol.png"

PRODUCTS = ["HYDROGEL_PACK", "VELVETFRUIT_EXTRACT"]
VOL_WINDOW = 100
TICKS_PER_DAY = 10_000


def load_mids():
    mids = {p: [] for p in PRODUCTS}
    for d in (0, 1, 2):
        with (DATA / f"prices_round_3_day_{d}.csv").open() as f:
            for r in csv.DictReader(f, delimiter=";"):
                if r["product"] in PRODUCTS and r["mid_price"]:
                    mids[r["product"]].append(float(r["mid_price"]))
    return mids


def rolling_std(series, window):
    a = np.asarray(series, dtype=float)
    diffs = np.diff(a)
    out = np.full(len(a), np.nan)
    for i in range(window, len(diffs) + 1):
        out[i] = diffs[i - window : i].std()
    return out


def main():
    mids = load_mids()

    fig, axes = plt.subplots(2, 2, figsize=(14, 8), sharex=True)
    fig.suptitle(
        "Round 3 — mid price and 100-tick rolling volatility (3-day continuous)",
        fontsize=12,
    )

    for col, product in enumerate(PRODUCTS):
        m = np.asarray(mids[product])
        x = np.arange(len(m))
        vol = rolling_std(m, VOL_WINDOW)

        ax_top = axes[0, col]
        ax_bot = axes[1, col]

        # mid
        ax_top.plot(x, m, lw=0.5, color="#1f77b4")
        ax_top.axhline(m.mean(), color="#888", ls="--", lw=0.6,
                       label=f"mean = {m.mean():.2f}")
        ax_top.set_title(f"{product}  (n={len(m):,} ticks)", fontsize=11)
        ax_top.set_ylabel("mid price")
        ax_top.legend(loc="upper right", fontsize=8)
        ax_top.grid(alpha=0.25)

        # vol
        ax_bot.plot(x, vol, lw=0.6, color="#d62728")
        ax_bot.axhline(np.nanmean(vol), color="#888", ls="--", lw=0.6,
                       label=f"mean vol = {np.nanmean(vol):.2f}")
        ax_bot.set_ylabel("rolling stdev (100-tick)")
        ax_bot.set_xlabel("tick index (concatenated D0 → D1 → D2)")
        ax_bot.legend(loc="upper right", fontsize=8)
        ax_bot.grid(alpha=0.25)

        # day boundary markers
        for d_end in (TICKS_PER_DAY, 2 * TICKS_PER_DAY):
            for ax in (ax_top, ax_bot):
                ax.axvline(d_end, color="black", lw=0.5, alpha=0.4)
        # day labels
        for i, label in enumerate(("D0", "D1", "D2")):
            ax_top.text(
                (i + 0.5) * TICKS_PER_DAY, ax_top.get_ylim()[1],
                label, ha="center", va="top", fontsize=9, color="#555",
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.8, pad=1),
            )

    plt.tight_layout(rect=(0, 0, 1, 0.96))
    plt.savefig(OUT, dpi=140, bbox_inches="tight")
    print(f"saved {OUT}")

    # text summary
    print("\nPer-product summary:")
    for product in PRODUCTS:
        m = np.asarray(mids[product])
        v = rolling_std(m, VOL_WINDOW)
        print(f"  {product}: mid mean={m.mean():.1f}  std={m.std():.1f}  "
              f"range=[{m.min():.1f}, {m.max():.1f}]  "
              f"rolling-vol mean={np.nanmean(v):.2f}  max={np.nanmax(v):.2f}")


if __name__ == "__main__":
    main()
