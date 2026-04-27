"""
Mid-price chart per product with Mark-trader BUY trades overlaid as
markers. "High bid volume" = trades where a Mark was the buyer; marker
size scales with trade quantity. Each Mark gets a distinct color so you
can see which counterparty is absorbing inventory at which price level.

Default: round-4 day 1. Pass an arg ("round3 0", "round4 2", etc.) to
switch days.

Saves a multi-panel PNG to analytics/.
"""
import sys
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "analytics"
OUT_DIR.mkdir(exist_ok=True)

ASSET_ORDER = [
    "HYDROGEL_PACK", "VELVETFRUIT_EXTRACT",
    "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100",
    "VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500",
]

MARK_COLORS = {
    "Mark 01": "#2563eb",
    "Mark 14": "#059669",
    "Mark 22": "#d97706",
    "Mark 38": "#dc2626",
    "Mark 49": "#7c3aed",
    "Mark 55": "#0891b2",
    "Mark 67": "#db2777",
}


def main() -> None:
    if len(sys.argv) >= 3:
        ds, day = sys.argv[1], int(sys.argv[2])
    else:
        ds, day = "round4", 1

    prices_path = REPO / f"backtester/datasets/{ds}/prices_{ds[:5]}_{ds[5:]}_day_{day}.csv"
    trades_path = REPO / f"backtester/datasets/{ds}/trades_{ds[:5]}_{ds[5:]}_day_{day}.csv"
    prices = pd.read_csv(prices_path, sep=";")
    trades = pd.read_csv(trades_path, sep=";")

    # Top-quantile threshold for "high" bid volume per asset (top 25%).
    thresholds = (
        trades[trades["buyer"].str.startswith("Mark", na=False)]
        .groupby("symbol")["quantity"]
        .quantile(0.75)
        .to_dict()
    )

    fig, axes = plt.subplots(len(ASSET_ORDER), 1, figsize=(16, 3.0 * len(ASSET_ORDER)), sharex=True)
    if len(ASSET_ORDER) == 1:
        axes = [axes]

    label_seen: set[str] = set()

    for ax, sym in zip(axes, ASSET_ORDER):
        psub = prices[prices["product"] == sym].sort_values("timestamp")
        if psub.empty:
            ax.set_visible(False)
            continue
        ax.plot(psub["timestamp"], psub["mid_price"], color="#111827",
                linewidth=1.1, label="mid", zorder=2)

        tsub = trades[
            (trades["symbol"] == sym)
            & trades["buyer"].str.startswith("Mark", na=False)
        ]
        thr = thresholds.get(sym, 0)
        hi = tsub[tsub["quantity"] >= thr]

        for mark in sorted(hi["buyer"].unique()):
            sub = hi[hi["buyer"] == mark]
            label = mark if mark not in label_seen else None
            label_seen.add(mark)
            ax.scatter(sub["timestamp"], sub["price"],
                       s=np.clip(sub["quantity"] * 12, 36, 400),
                       c=MARK_COLORS.get(mark, "#6b7280"),
                       alpha=0.75, edgecolors="white", linewidths=0.6,
                       label=label, zorder=3)

        ax.set_title(f"{sym}    (highlight threshold: qty ≥ {thr:.0f})",
                     fontsize=11, loc="left", color="#374151")
        ax.grid(axis="y", linestyle="--", alpha=0.3)
        ax.set_axisbelow(True)
        ax.tick_params(axis="both", labelsize=9)
        ax.set_ylabel("price", fontsize=9)

    fig.suptitle(
        f"Mid price + Mark BUY events (top-25% qty)  —  {ds} day {day}",
        fontsize=13, y=0.995,
    )
    fig.supxlabel("timestamp", fontsize=11)

    handles, labels = [], []
    for ax in axes:
        for h, l in zip(*ax.get_legend_handles_labels()):
            if l and l != "mid" and l not in labels:
                handles.append(h); labels.append(l)
    fig.legend(handles, labels, loc="upper right", ncol=len(labels),
               bbox_to_anchor=(0.99, 0.985), fontsize=10, title="trader")

    plt.tight_layout(rect=[0.02, 0.01, 1.0, 0.97])
    out = OUT_DIR / f"mid_with_marks_{ds}_d{day}.png"
    plt.savefig(out, dpi=130)
    plt.close(fig)
    print(f"Saved {out.relative_to(REPO)}")

    # Per-asset PNGs for high-resolution inspection
    asset_dir = OUT_DIR / f"mid_with_marks_{ds}_d{day}_per_asset"
    asset_dir.mkdir(exist_ok=True)
    for sym in ASSET_ORDER:
        psub = prices[prices["product"] == sym].sort_values("timestamp")
        if psub.empty:
            continue
        f, a = plt.subplots(figsize=(14, 5))
        a.plot(psub["timestamp"], psub["mid_price"], color="#111827",
               linewidth=1.2, label="mid", zorder=2)
        tsub = trades[(trades["symbol"] == sym)
                      & trades["buyer"].str.startswith("Mark", na=False)]
        thr = thresholds.get(sym, 0)
        hi = tsub[tsub["quantity"] >= thr]
        for mark in sorted(hi["buyer"].unique()):
            sub = hi[hi["buyer"] == mark]
            a.scatter(sub["timestamp"], sub["price"],
                      s=np.clip(sub["quantity"] * 14, 50, 500),
                      c=MARK_COLORS.get(mark, "#6b7280"),
                      alpha=0.75, edgecolors="white", linewidths=0.7,
                      label=mark, zorder=3)
        a.set_title(f"{sym}  —  {ds} day {day}  (highlight: bid qty ≥ {thr:.0f})", fontsize=12)
        a.set_xlabel("timestamp"); a.set_ylabel("price")
        a.legend(loc="best", fontsize=10, title="trader (buy side)")
        a.grid(axis="y", linestyle="--", alpha=0.3); a.set_axisbelow(True)
        plt.tight_layout()
        out_a = asset_dir / f"{sym}.png"
        plt.savefig(out_a, dpi=140)
        plt.close(f)
    print(f"Per-asset PNGs in {asset_dir.relative_to(REPO)}")


if __name__ == "__main__":
    main()
