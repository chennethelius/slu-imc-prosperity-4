"""
Plot bid (buy) volume per asset by trader, aggregated across all 4 days
(round-3 day 0 + round-4 days 1–3).

Each market trade in trades_*.csv has a `buyer` and `seller` field tagged
"Mark NN". Bid volume by trader X on asset A = sum(qty) where buyer == X
and symbol == A. This shows which Marks are absorbing inventory on each
product — useful for identifying counterparty patterns.

Saves PNG to analytics/trader_bid_volumes.png and prints a CSV-ish
summary to stdout.
"""
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRADES = [
    REPO / "backtester/datasets/round3/trades_round_3_day_0.csv",
    REPO / "backtester/datasets/round4/trades_round_4_day_1.csv",
    REPO / "backtester/datasets/round4/trades_round_4_day_2.csv",
    REPO / "backtester/datasets/round4/trades_round_4_day_3.csv",
]
OUT_DIR = REPO / "analytics"
OUT_DIR.mkdir(exist_ok=True)
OUT_PNG = OUT_DIR / "trader_bid_volumes.png"

# Asset display order: stable / underlying products first, then the strike
# chain. VEV_6000 / VEV_6500 are pinned at mid 0.5 so volumes there are
# nominal — keep them but expect ~0.
ASSET_ORDER = [
    "HYDROGEL_PACK", "VELVETFRUIT_EXTRACT",
    "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100", "VEV_5200",
    "VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500",
]


def main() -> None:
    frames = []
    for p in TRADES:
        df = pd.read_csv(p, sep=";")
        frames.append(df)
    trades = pd.concat(frames, ignore_index=True)

    # Aggregate bid (buy-side) volume per (trader, asset)
    pivot = (
        trades.groupby(["buyer", "symbol"])["quantity"].sum()
        .unstack(fill_value=0)
        .reindex(columns=[a for a in ASSET_ORDER if a in trades["symbol"].unique()])
        .fillna(0)
        .astype(int)
    )
    # Rank traders by total bid volume (descending) for stable visual order
    pivot["__total__"] = pivot.sum(axis=1)
    pivot = pivot.sort_values("__total__", ascending=False)
    pivot = pivot.drop(columns="__total__")

    print("Bid volume by trader × asset (4-day aggregate):")
    print(pivot.to_string())
    print()
    print("Total bid volume per trader:")
    print(pivot.sum(axis=1).sort_values(ascending=False).to_string())

    # Plot: grouped bar chart (asset on x, bars per trader)
    fig, ax = plt.subplots(figsize=(14, 6))
    n_traders = len(pivot.index)
    n_assets = len(pivot.columns)
    x = np.arange(n_assets)
    bar_w = 0.85 / n_traders
    cmap = plt.get_cmap("tab10")

    for i, trader in enumerate(pivot.index):
        offset = (i - (n_traders - 1) / 2) * bar_w
        vals = pivot.loc[trader].values
        ax.bar(x + offset, vals, bar_w, label=trader, color=cmap(i % 10), edgecolor="white", linewidth=0.5)

    ax.set_xticks(x)
    ax.set_xticklabels(pivot.columns, rotation=35, ha="right", fontsize=10)
    ax.set_ylabel("Total bid (buy) volume", fontsize=11)
    ax.set_title("Bid volume per asset by trader  —  4-day aggregate (r3 d0 + r4 d1-3)", fontsize=12, pad=12)
    ax.legend(title="trader", loc="upper right", fontsize=9, ncol=2)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ax.set_axisbelow(True)
    plt.tight_layout()
    plt.savefig(OUT_PNG, dpi=140)
    print(f"\nSaved chart to {OUT_PNG.relative_to(REPO)}")


if __name__ == "__main__":
    main()
