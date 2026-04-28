"""
Mid-price chart per product with Mark-trader trades overlaid as markers.

Buys are upward triangles (▲), sells are downward triangles (▼). Each
Mark gets a distinct color. Marker size scales with trade quantity.
Default threshold: top-25% of trade quantity per asset across BOTH
sides (so the markers shown represent the larger trades).

Default day: round-4 day 1. Pass a day spec ("round3 0", "round4 2", ...)
as args to switch.

Saves a multi-panel composite PNG plus per-asset PNGs to analytics/.
"""
import sys
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.lines as mlines
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

# Buys keep full saturation; sells drop alpha to read the direction at a
# glance even when the same Mark trades both sides nearby in time.
ALPHA_BUY = 0.85
ALPHA_SELL = 0.55


def _is_mark(s: pd.Series) -> pd.Series:
    return s.fillna("").str.startswith("Mark")


def _plot_marks(ax, tsub_buy, tsub_sell, thr, sizescale=12, sizemin=36, sizemax=400):
    """Plot scatter markers for buys + sells onto `ax`. Returns set of
    Mark labels actually plotted (for legend dedupe)."""
    seen = set()
    # Buys: triangle up, full alpha
    for mark in sorted(tsub_buy["buyer"].dropna().unique()):
        sub = tsub_buy[tsub_buy["buyer"] == mark]
        if sub.empty:
            continue
        ax.scatter(sub["timestamp"], sub["price"],
                   s=np.clip(sub["quantity"] * sizescale, sizemin, sizemax),
                   c=MARK_COLORS.get(mark, "#6b7280"),
                   alpha=ALPHA_BUY, edgecolors="white", linewidths=0.6,
                   marker="^", zorder=3)
        seen.add(mark)
    # Sells: triangle down, lower alpha
    for mark in sorted(tsub_sell["seller"].dropna().unique()):
        sub = tsub_sell[tsub_sell["seller"] == mark]
        if sub.empty:
            continue
        ax.scatter(sub["timestamp"], sub["price"],
                   s=np.clip(sub["quantity"] * sizescale, sizemin, sizemax),
                   c=MARK_COLORS.get(mark, "#6b7280"),
                   alpha=ALPHA_SELL, edgecolors="white", linewidths=0.6,
                   marker="v", zorder=3)
        seen.add(mark)
    return seen


def main() -> None:
    if len(sys.argv) >= 3:
        ds, day = sys.argv[1], int(sys.argv[2])
    else:
        ds, day = "round4", 1

    prices_path = REPO / f"backtester/datasets/{ds}/prices_{ds[:5]}_{ds[5:]}_day_{day}.csv"
    trades_path = REPO / f"backtester/datasets/{ds}/trades_{ds[:5]}_{ds[5:]}_day_{day}.csv"
    prices = pd.read_csv(prices_path, sep=";")
    trades = pd.read_csv(trades_path, sep=";")

    # Threshold per asset = 75th-percentile quantity across all Mark trades
    # (buys + sells combined). Larger trades get markered.
    mark_trades = trades[_is_mark(trades["buyer"]) | _is_mark(trades["seller"])]
    thresholds = (
        mark_trades.groupby("symbol")["quantity"].quantile(0.75).to_dict()
    )

    # === Composite: one stacked column with all assets ===
    fig, axes = plt.subplots(len(ASSET_ORDER), 1, figsize=(16, 3.0 * len(ASSET_ORDER)), sharex=True)
    if len(ASSET_ORDER) == 1:
        axes = [axes]

    all_marks_seen: set[str] = set()

    for ax, sym in zip(axes, ASSET_ORDER):
        psub = prices[prices["product"] == sym].sort_values("timestamp")
        if psub.empty:
            ax.set_visible(False)
            continue
        ax.plot(psub["timestamp"], psub["mid_price"], color="#111827",
                linewidth=1.1, label="mid", zorder=2)

        tsub = trades[trades["symbol"] == sym]
        thr = thresholds.get(sym, 0)
        hi = tsub[tsub["quantity"] >= thr]
        buys  = hi[_is_mark(hi["buyer"])]
        sells = hi[_is_mark(hi["seller"])]
        all_marks_seen |= _plot_marks(ax, buys, sells, thr)

        ax.set_title(f"{sym}    (highlight threshold: qty ≥ {thr:.0f})",
                     fontsize=11, loc="left", color="#374151")
        ax.grid(axis="y", linestyle="--", alpha=0.3)
        ax.set_axisbelow(True)
        ax.tick_params(axis="both", labelsize=9)
        ax.set_ylabel("price", fontsize=9)

    fig.suptitle(
        f"Mid price + Mark BUY (▲) / SELL (▼) events (top-25% qty)  —  {ds} day {day}",
        fontsize=13, y=0.995,
    )
    fig.supxlabel("timestamp", fontsize=11)

    # Single legend with one entry per Mark (color), plus shape legend.
    legend_handles = [
        mlines.Line2D([], [], color=MARK_COLORS.get(m, "#6b7280"), marker="o",
                      linestyle="None", markersize=9, label=m,
                      markeredgecolor="white", markeredgewidth=0.5)
        for m in sorted(all_marks_seen)
    ]
    legend_handles += [
        mlines.Line2D([], [], color="#374151", marker="^", linestyle="None",
                      markersize=9, label="buy", markeredgecolor="white", markeredgewidth=0.5),
        mlines.Line2D([], [], color="#374151", marker="v", linestyle="None",
                      markersize=9, label="sell", markeredgecolor="white",
                      markeredgewidth=0.5, alpha=ALPHA_SELL),
    ]
    fig.legend(handles=legend_handles, loc="upper right",
               ncol=len(legend_handles), bbox_to_anchor=(0.99, 0.985),
               fontsize=10, title="trader / side", title_fontsize=10)

    plt.tight_layout(rect=[0.02, 0.01, 1.0, 0.97])
    out = OUT_DIR / f"mid_with_marks_{ds}_d{day}.png"
    plt.savefig(out, dpi=130)
    plt.close(fig)
    print(f"Saved {out.relative_to(REPO)}")

    # === Per-asset PNGs ===
    asset_dir = OUT_DIR / f"mid_with_marks_{ds}_d{day}_per_asset"
    asset_dir.mkdir(exist_ok=True)
    for sym in ASSET_ORDER:
        psub = prices[prices["product"] == sym].sort_values("timestamp")
        if psub.empty:
            continue
        f, a = plt.subplots(figsize=(14, 5))
        a.plot(psub["timestamp"], psub["mid_price"], color="#111827",
               linewidth=1.2, label="mid", zorder=2)
        tsub = trades[trades["symbol"] == sym]
        thr = thresholds.get(sym, 0)
        hi = tsub[tsub["quantity"] >= thr]
        buys  = hi[_is_mark(hi["buyer"])]
        sells = hi[_is_mark(hi["seller"])]
        marks_here = _plot_marks(a, buys, sells, thr,
                                 sizescale=14, sizemin=50, sizemax=500)

        # Custom legend with mark colors + shape key
        handles = [mlines.Line2D([], [], color=MARK_COLORS.get(m, "#6b7280"),
                                 marker="o", linestyle="None", markersize=9, label=m,
                                 markeredgecolor="white", markeredgewidth=0.5)
                   for m in sorted(marks_here)]
        handles += [
            mlines.Line2D([], [], color="#374151", marker="^", linestyle="None",
                          markersize=9, label="buy"),
            mlines.Line2D([], [], color="#374151", marker="v", linestyle="None",
                          markersize=9, label="sell", alpha=ALPHA_SELL),
        ]
        a.set_title(f"{sym}  —  {ds} day {day}  (highlight: qty ≥ {thr:.0f})", fontsize=12)
        a.set_xlabel("timestamp"); a.set_ylabel("price")
        a.legend(handles=handles, loc="best", fontsize=10, title="trader / side")
        a.grid(axis="y", linestyle="--", alpha=0.3); a.set_axisbelow(True)
        plt.tight_layout()
        out_a = asset_dir / f"{sym}.png"
        plt.savefig(out_a, dpi=140)
        plt.close(f)
    print(f"Per-asset PNGs in {asset_dir.relative_to(REPO)}")


if __name__ == "__main__":
    main()
