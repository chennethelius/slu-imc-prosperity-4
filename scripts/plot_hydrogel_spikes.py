"""
HYDROGEL_PACK day-2 spike-window forensics.

For each major price-spike event, zoom in on the 1500-tick window around
it and overlay:
  - mid price
  - trade markers (size = qty, color = aggressor side)
  - TOB imbalance signal
  - bid/ask spread

Goal: see whether a *pre-spike signature* exists in volume, aggressor
imbalance, or spread — i.e., whether you could position before the
move starts rather than mean-reverting after it ends.

Output: notebooks/hydrogel_spikes.png
"""

import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "backtester" / "datasets" / "round3"
OUT = REPO / "notebooks" / "hydrogel_spikes.png"


def load():
    book = {}  # ts → (bb, ba, bv, av, mid)
    with (DATA / "prices_round_3_day_2.csv").open() as f:
        for r in csv.DictReader(f, delimiter=";"):
            if r["product"] != "HYDROGEL_PACK" or not r["mid_price"]:
                continue
            book[int(r["timestamp"])] = (
                int(r["bid_price_1"]), int(r["ask_price_1"]),
                int(r["bid_volume_1"] or 0), int(r["ask_volume_1"] or 0),
                float(r["mid_price"]),
            )
    trades = []
    with (DATA / "trades_round_3_day_2.csv").open() as f:
        for r in csv.DictReader(f, delimiter=";"):
            if r["symbol"] != "HYDROGEL_PACK":
                continue
            trades.append((int(r["timestamp"]), float(r["price"]), int(r["quantity"])))
    return book, trades


def find_spikes(book, n=4, window=2000):
    """Find n disjoint windows where mid moves the most over `window` ticks."""
    ts_arr = np.array(sorted(book.keys()))
    mids = np.array([book[t][4] for t in ts_arr])
    # Sample the window-wide change at every starting tick
    candidates = []
    for i in range(len(mids) - window // 100):
        change = mids[i + window // 100] - mids[i]
        candidates.append((abs(change), int(ts_arr[i]), int(ts_arr[i + window // 100]), change))
    candidates.sort(reverse=True)
    chosen = []
    for c in candidates:
        if all(abs(c[1] - x[1]) > window for x in chosen):
            chosen.append(c)
            if len(chosen) >= n:
                break
    return chosen


def panel_for(ax, ax2, book, trades, t0, t1, label):
    """Plot a zoom around [t0,t1] showing mid, trades, imbalance, spread."""
    pad = 800
    lo, hi = t0 - pad, t1 + pad
    book_window = sorted([(t, *v) for t, v in book.items() if lo <= t <= hi])
    if not book_window:
        return
    ts = np.array([b[0] for b in book_window])
    bb = np.array([b[1] for b in book_window])
    ba = np.array([b[2] for b in book_window])
    bv = np.array([b[3] for b in book_window])
    av = np.array([b[4] for b in book_window])
    mid = np.array([b[5] for b in book_window])
    spread = ba - bb
    imb = (bv - av) / np.maximum(bv + av, 1)

    ax.plot(ts, mid, lw=0.8, color="#222", label="mid")
    ax.axvspan(t0, t1, alpha=0.12, color="#ff7f0e", lw=0)

    mid_lookup = {t: m for t, _, _, _, _, m in book_window}
    for tt, px, qty in trades:
        if not (lo <= tt <= hi):
            continue
        m = mid_lookup.get(tt)
        if m is None:
            i = np.searchsorted(ts, tt)
            i = min(max(i, 0), len(mid) - 1)
            m = mid[i]
        side = "buy" if px > m else "sell" if px < m else "mid"
        c = "#2ca02c" if side == "buy" else "#d62728" if side == "sell" else "#888"
        ax.scatter(tt, px, s=qty * 12, c=c, alpha=0.8, edgecolors="white", lw=0.4, zorder=4)

    ax.set_ylabel("price", color="#222", fontsize=9)
    ax.grid(alpha=0.25)
    ax.set_title(label, fontsize=10)

    ax2.plot(ts, imb, lw=0.7, color="#1f77b4", alpha=0.9, label="TOB imb")
    ax2.axhline(0, color="#aaa", lw=0.5)
    ax2.fill_between(ts, 0, imb, where=imb > 0, color="#2ca02c", alpha=0.15)
    ax2.fill_between(ts, 0, imb, where=imb < 0, color="#d62728", alpha=0.15)
    ax2.set_ylim(-1.1, 1.1)
    ax2.set_ylabel("TOB imb", color="#1f77b4", fontsize=9)
    ax2.tick_params(axis="y", labelcolor="#1f77b4", labelsize=8)


def main():
    book, trades = load()
    spikes = find_spikes(book, n=4, window=2000)
    print("Top spike windows:")
    for mag, t0, t1, signed in spikes:
        print(f"  ts {t0:>6} → {t1:<6}  Δ={signed:+.0f}  ({mag:.0f} mag)")

    fig, axes = plt.subplots(len(spikes), 1, figsize=(13, 3 * len(spikes)),
                             sharex=False)
    if len(spikes) == 1:
        axes = [axes]
    for ax, (mag, t0, t1, signed) in zip(axes, spikes):
        ax2 = ax.twinx()
        direction = "↑" if signed > 0 else "↓"
        panel_for(ax, ax2, book, trades, t0, t1,
                  f"ts {t0}-{t1}  {direction} {abs(signed):.0f} ticks (mid {book[t0][4]:.0f}→{book[t1][4]:.0f})")
        ax.legend(loc="upper left", fontsize=8)
        ax2.legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel("timestamp")
    fig.suptitle(
        "HYDROGEL_PACK day-2 spike forensics — pre-spike signal vs price",
        fontsize=12, y=0.995,
    )
    plt.tight_layout()
    plt.savefig(OUT, dpi=140, bbox_inches="tight")
    print(f"saved {OUT}")

    # ---- pre-spike statistics  (timestamps are in 100-unit increments, so
    # multiply tick-count by 100 to get the timestamp span)
    pre_window = 50_000   # 500 trading ticks before spike start
    print(f"\nPRE-SPIKE SIGNATURES (last {pre_window // 100} ticks before spike start):")
    print(f"{'event':>22}  {'avg_imb':>9}  {'pre_vol':>8}  {'pre_n':>6}  {'avg_spr':>8}")
    for mag, t0, t1, signed in spikes:
        ts_pre = [t for t in book if t0 - pre_window <= t < t0]
        if not ts_pre:
            continue
        imbs, spreads = [], []
        for t in ts_pre:
            bb, ba, bv, av, _ = book[t]
            spreads.append(ba - bb)
            imbs.append((bv - av) / max(bv + av, 1))
        pre_trades = [t for t in trades if t0 - pre_window <= t[0] < t0]
        avg_imb = np.mean(imbs)
        pre_vol = sum(t[2] for t in pre_trades)
        avg_spr = np.mean(spreads)
        print(f"  ts {t0:>6}({'+' if signed>0 else '-'}{abs(signed):.0f}): "
              f" {avg_imb:>+8.3f}  {pre_vol:>8}  {len(pre_trades):>6}  {avg_spr:>8.1f}")

    # baseline averages for context
    all_imbs, all_spreads, all_n_trades, all_vols = [], [], [], []
    ts_keys = sorted(book.keys())
    win_ticks = pre_window // 100  # number of book entries per window
    for i in range(0, len(ts_keys) - win_ticks, win_ticks):
        sub = ts_keys[i:i + win_ticks]
        if not sub:
            continue
        imbs = [(book[t][2] - book[t][3]) / max(book[t][2] + book[t][3], 1) for t in sub]
        spreads = [book[t][1] - book[t][0] for t in sub]
        all_imbs.append(np.mean(imbs))
        all_spreads.append(np.mean(spreads))
        sub_trades = [t for t in trades if sub[0] <= t[0] < sub[-1]]
        all_n_trades.append(len(sub_trades))
        all_vols.append(sum(t[2] for t in sub_trades))
    print(f"\nBASELINE (random {pre_window // 100}-tick windows):")
    print(f"  avg_imb={np.mean(all_imbs):+.3f}  avg_vol={np.mean(all_vols):.1f}  "
          f"avg_n_trades={np.mean(all_n_trades):.1f}  avg_spread={np.mean(all_spreads):.1f}")


if __name__ == "__main__":
    main()
