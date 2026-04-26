"""
HYDROGEL_PACK day-2 trade-volume analysis for bot-behavior inference.

Plots:
  (1) mid price across the day with trade markers (size = qty, color = aggressor side)
  (2) rolling-window trade volume vs price volatility (do bots fire on big moves?)
  (3) trade size distribution
  (4) trade price relative to mid at trade time (aggressor histogram)

Aggressor side is inferred from trade price vs concurrent mid:
  trade_price > mid  → BUY-aggressor (lifted an ask)
  trade_price < mid  → SELL-aggressor (hit a bid)
  trade_price = mid  → ambiguous (passive cross)

Output: notebooks/hydrogel_volume.png
"""

import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "backtester" / "datasets" / "round3"
OUT = REPO / "notebooks" / "hydrogel_volume.png"

WINDOW = 500   # rolling-window length for volume / volatility


def load_trades():
    out = []
    with (DATA / "trades_round_3_day_2.csv").open() as f:
        for r in csv.DictReader(f, delimiter=";"):
            if r["symbol"] != "HYDROGEL_PACK":
                continue
            out.append((int(r["timestamp"]), float(r["price"]), int(r["quantity"])))
    return out


def load_mids():
    out = []
    with (DATA / "prices_round_3_day_2.csv").open() as f:
        for r in csv.DictReader(f, delimiter=";"):
            if r["product"] != "HYDROGEL_PACK" or not r["mid_price"]:
                continue
            out.append((int(r["timestamp"]), float(r["mid_price"])))
    return out


def main():
    trades = load_trades()
    mids = load_mids()
    ts_arr = np.array([m[0] for m in mids])
    mid_arr = np.array([m[1] for m in mids])

    def mid_at(ts):
        i = np.searchsorted(ts_arr, ts)
        if i >= len(ts_arr):
            return mid_arr[-1]
        return mid_arr[i]

    trade_sides = []
    for ts, px, qty in trades:
        m = mid_at(ts)
        side = "buy" if px > m else "sell" if px < m else "mid"
        trade_sides.append((ts, px, qty, side, m))

    fig = plt.figure(figsize=(14, 9))
    gs = fig.add_gridspec(3, 2, height_ratios=[3, 2, 1.5], hspace=0.4, wspace=0.25)
    ax_top = fig.add_subplot(gs[0, :])
    ax_mid = fig.add_subplot(gs[1, :], sharex=ax_top)
    ax_h_size = fig.add_subplot(gs[2, 0])
    ax_h_offset = fig.add_subplot(gs[2, 1])

    # ---- top: mid + trade markers
    ax_top.plot(ts_arr, mid_arr, lw=0.45, color="#444", label="HYDROGEL mid")
    color_for = {"buy": "#2ca02c", "sell": "#d62728", "mid": "#888"}
    for ts, px, qty, side, _ in trade_sides:
        ax_top.scatter(ts, px, s=qty * 6, c=color_for[side], alpha=0.65,
                       edgecolors="white", lw=0.4, zorder=3)
    for label, color in color_for.items():
        ax_top.scatter([], [], s=24, c=color, label=f"{label}-aggressor")
    ax_top.set_ylabel("price")
    ax_top.set_title(
        f"HYDROGEL_PACK day 2 — {len(trades)} trades, {sum(t[2] for t in trades)} units"
    )
    ax_top.legend(fontsize=8, loc="upper right")
    ax_top.grid(alpha=0.25)

    # ---- middle: rolling volume vs rolling abs-mid-change (volatility proxy)
    bins = np.arange(0, 1_000_001, 5_000)
    bin_vol = np.zeros(len(bins) - 1)
    for ts, _, qty, _, _ in trade_sides:
        b = np.searchsorted(bins, ts) - 1
        if 0 <= b < len(bin_vol):
            bin_vol[b] += qty
    bin_centers = (bins[:-1] + bins[1:]) / 2

    # rolling vol of mid changes per same bins
    bin_var = np.zeros(len(bins) - 1)
    for i in range(len(bins) - 1):
        mask = (ts_arr >= bins[i]) & (ts_arr < bins[i + 1])
        sub = mid_arr[mask]
        if len(sub) > 2:
            bin_var[i] = np.std(np.diff(sub))

    ax_mid.bar(bin_centers, bin_vol, width=4500, color="#1f77b4", alpha=0.55,
               label="trade volume per 5k-tick window")
    ax_mid2 = ax_mid.twinx()
    ax_mid2.plot(bin_centers, bin_var, color="#d62728", lw=1.4,
                 label="rolling 1-tick stdev")
    ax_mid.set_ylabel("volume", color="#1f77b4")
    ax_mid2.set_ylabel("price stdev", color="#d62728")
    ax_mid.set_xlabel("timestamp")
    ax_mid.legend(fontsize=8, loc="upper left")
    ax_mid2.legend(fontsize=8, loc="upper right")
    ax_mid.grid(alpha=0.25)

    corr = np.corrcoef(bin_vol, bin_var)[0, 1] if bin_vol.std() and bin_var.std() else 0.0
    ax_mid.set_title(
        f"trade volume vs price volatility — corr = {corr:+.2f}",
        fontsize=10,
    )

    # ---- size hist
    sizes = [t[2] for t in trades]
    ax_h_size.hist(sizes, bins=range(min(sizes), max(sizes) + 2),
                   color="#1f77b4", edgecolor="white", lw=0.4)
    ax_h_size.set_title("trade size distribution", fontsize=10)
    ax_h_size.set_xlabel("qty")
    ax_h_size.set_ylabel("count")
    ax_h_size.grid(alpha=0.25)

    # ---- offset from mid hist
    offsets = [px - m for _, px, _, _, m in trade_sides]
    ax_h_offset.hist(offsets, bins=20, color="#9467bd",
                     edgecolor="white", lw=0.4)
    ax_h_offset.axvline(0, color="black", lw=0.7, ls="--")
    ax_h_offset.set_title("trade price − concurrent mid", fontsize=10)
    ax_h_offset.set_xlabel("offset (price ticks)")
    ax_h_offset.set_ylabel("count")
    ax_h_offset.grid(alpha=0.25)

    plt.savefig(OUT, dpi=140, bbox_inches="tight")
    print(f"saved {OUT}")

    # ---- text stats
    n_buy = sum(1 for s in trade_sides if s[3] == "buy")
    n_sell = sum(1 for s in trade_sides if s[3] == "sell")
    n_mid = sum(1 for s in trade_sides if s[3] == "mid")
    v_buy = sum(s[2] for s in trade_sides if s[3] == "buy")
    v_sell = sum(s[2] for s in trade_sides if s[3] == "sell")
    print(f"\nAggressor split:")
    print(f"  buy-side  {n_buy:>4} trades, vol {v_buy}")
    print(f"  sell-side {n_sell:>4} trades, vol {v_sell}")
    print(f"  ambiguous {n_mid:>4} trades")
    print(f"\nVolume / volatility correlation: {corr:+.3f}")


if __name__ == "__main__":
    main()
