"""
Plot HYDROGEL_PACK mid prices vs candidate "true fair value" anchors:

  1. expanding-window mean (averages across all days; slow-tracking)
  2. rolling-window mean over WINDOW ticks (per-day-aware)

Shows the distribution of (mid − anchor) so a divergence threshold can be
chosen from the data instead of guessed.

Output: notebooks/hydrogel_anchor.png
"""

import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "backtester" / "datasets" / "round3"
OUT = REPO / "notebooks" / "hydrogel_anchor.png"

ROLLING_WINDOW = 5000        # rolling-mean window for the per-day-aware anchor


def load_mids() -> tuple[np.ndarray, np.ndarray, list[int]]:
    rows, boundaries = [], []
    for day in (0, 1, 2):
        with (DATA / f"prices_round_3_day_{day}.csv").open() as f:
            for r in csv.DictReader(f, delimiter=";"):
                if r["product"] != "HYDROGEL_PACK" or not r["mid_price"]:
                    continue
                ts = int(r["timestamp"]) + day * 1_000_000
                rows.append((ts, float(r["mid_price"])))
        boundaries.append(len(rows))
    rows.sort()
    return np.array([t for t, _ in rows]), np.array([m for _, m in rows]), boundaries


def expanding_mean(values):
    return np.cumsum(values) / np.arange(1, len(values) + 1)


def rolling_mean(values, window):
    cs = np.cumsum(np.insert(values, 0, 0))
    out = np.empty(len(values))
    for i in range(len(values)):
        start = max(0, i - window + 1)
        out[i] = (cs[i + 1] - cs[start]) / (i + 1 - start)
    return out


def main():
    ts, mids, boundaries = load_mids()
    anchor_exp = expanding_mean(mids)
    anchor_roll = rolling_mean(mids, ROLLING_WINDOW)

    diverge_exp = mids - anchor_exp
    diverge_roll = mids - anchor_roll

    fig = plt.figure(figsize=(13, 8.5))
    gs = fig.add_gridspec(3, 2, height_ratios=[3, 1, 2], hspace=0.35, wspace=0.25)
    ax_top = fig.add_subplot(gs[0, :])
    ax_div = fig.add_subplot(gs[1, :], sharex=ax_top)
    ax_h_exp = fig.add_subplot(gs[2, 0])
    ax_h_roll = fig.add_subplot(gs[2, 1])

    ax_top.plot(ts, mids, lw=0.5, color="#444", label="HYDROGEL mid")
    ax_top.plot(ts, anchor_exp, lw=1.4, color="#1f77b4",
                label="expanding-mean anchor")
    ax_top.plot(ts, anchor_roll, lw=1.4, color="#d62728",
                label=f"rolling-{ROLLING_WINDOW} anchor")
    for b in boundaries[:-1]:
        ax_top.axvline(ts[b - 1], color="#999", ls="--", lw=0.7)
    ax_top.set_ylabel("price")
    ax_top.set_title("HYDROGEL_PACK — mid vs candidate anchors (round 3)")
    ax_top.legend(fontsize=8, loc="upper left")
    ax_top.grid(alpha=0.25)

    ax_div.plot(ts, diverge_exp, lw=0.5, color="#1f77b4", alpha=0.6,
                label="mid − expanding-mean")
    ax_div.plot(ts, diverge_roll, lw=0.5, color="#d62728", alpha=0.6,
                label=f"mid − rolling-{ROLLING_WINDOW}")
    ax_div.axhline(0, color="#999", lw=0.7)
    for b in boundaries[:-1]:
        ax_div.axvline(ts[b - 1], color="#999", ls="--", lw=0.7)
    ax_div.set_ylabel("divergence")
    ax_div.set_xlabel("timestamp")
    ax_div.legend(fontsize=8, loc="upper left")
    ax_div.grid(alpha=0.25)

    for ax, data, title, color in (
        (ax_h_exp, diverge_exp, "expanding-mean", "#1f77b4"),
        (ax_h_roll, diverge_roll, f"rolling-{ROLLING_WINDOW}", "#d62728"),
    ):
        ax.hist(data, bins=80, color=color, alpha=0.7, edgecolor="white", lw=0.3)
        for q in (0.05, 0.25, 0.75, 0.95):
            v = np.quantile(data, q)
            ax.axvline(v, color="black", ls="--", lw=0.6)
            ax.text(v, ax.get_ylim()[1] * 0.95, f"{int(q*100)}%\n{v:+.1f}",
                    fontsize=7, ha="center", va="top")
        ax.set_title(f"distribution of (mid − {title})", fontsize=10)
        ax.set_xlabel("ticks above/below anchor")
        ax.grid(alpha=0.25)

    plt.savefig(OUT, dpi=140, bbox_inches="tight")
    print(f"saved {OUT}")

    print(f"\nDIVERGENCE STATISTICS (after warmup):")
    for label, data in (("expanding-mean", diverge_exp[5000:]),
                        (f"rolling-{ROLLING_WINDOW}", diverge_roll[ROLLING_WINDOW:])):
        std = float(np.std(data))
        q05, q95 = float(np.quantile(data, 0.05)), float(np.quantile(data, 0.95))
        q01, q99 = float(np.quantile(data, 0.01)), float(np.quantile(data, 0.99))
        print(f"  {label}:")
        print(f"    std = {std:6.2f}    1%/99% = {q01:+6.1f} / {q99:+6.1f}"
              f"    5%/95% = {q05:+6.1f} / {q95:+6.1f}")


if __name__ == "__main__":
    main()
