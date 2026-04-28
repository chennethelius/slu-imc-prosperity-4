"""
Per-Mark predictive alpha analysis.

For each (Mark, product, side) tuple, compute the average forward return
at multiple horizons:

  alpha_buy(Δt)  = mid(t + Δt) - trade_price   (positive = Mark bought
                                                cheap relative to future)
  alpha_sell(Δt) = trade_price - mid(t + Δt)   (positive = Mark sold rich)

If a Mark consistently has POSITIVE alpha → they're informed; we should
shadow their direction. If consistently NEGATIVE → they're noise traders;
we should fade them.

t-statistic (mean / sd-of-mean × sqrt(N)) flags statistical significance.
|t| > 2 → ~5% significance, |t| > 3 → ~0.3%.

Aggregates across round-3 d0 + round-4 d1-3 (40,000 ticks total).

Usage:  python scripts/mark_alpha.py
"""
import pandas as pd
import numpy as np
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DAYS = [
    ("round3", 0),
    ("round4", 1),
    ("round4", 2),
    ("round4", 3),
]
HORIZONS = [100, 500, 1000, 5000]  # in ticks


def _load_day(ds: str, d: int):
    prefix = f"{ds[:5]}_{ds[5:]}_day_{d}"
    prices = pd.read_csv(REPO / f"backtester/datasets/{ds}/prices_{prefix}.csv", sep=";")
    trades = pd.read_csv(REPO / f"backtester/datasets/{ds}/trades_{prefix}.csv", sep=";")
    prices = prices.dropna(subset=["mid_price"])[["timestamp", "product", "mid_price"]]
    return prices, trades


def _future_mid(prices_by_sym: dict, sym: str, ts: int, dt: int) -> float | None:
    pdf = prices_by_sym.get(sym)
    if pdf is None or pdf.empty:
        return None
    target = ts + dt
    # Forward-fill: pick last quote at or before target
    idx = pdf["timestamp"].searchsorted(target, side="right") - 1
    if idx < 0:
        return None
    return float(pdf.iloc[idx]["mid_price"])


def main() -> None:
    # Per-day price tables for fast lookup
    rows = []
    for ds, d in DAYS:
        prices, trades = _load_day(ds, d)
        prices_by_sym = {
            sym: g.sort_values("timestamp").reset_index(drop=True)
            for sym, g in prices.groupby("product")
        }
        for t in trades.itertuples(index=False):
            ts = int(t.timestamp); price = float(t.price); qty = int(t.quantity)
            buyer, seller = t.buyer, t.seller
            for dt in HORIZONS:
                fmid = _future_mid(prices_by_sym, t.symbol, ts, dt)
                if fmid is None:
                    continue
                if isinstance(buyer, str) and buyer.startswith("Mark"):
                    rows.append((buyer, t.symbol, "buy", dt, qty, fmid - price, ds, d))
                if isinstance(seller, str) and seller.startswith("Mark"):
                    rows.append((seller, t.symbol, "sell", dt, qty, price - fmid, ds, d))
    df = pd.DataFrame(rows, columns=["mark", "sym", "side", "dt", "qty", "alpha", "ds", "d"])
    print(f"Loaded {len(df):,} (mark, side, horizon) observations")

    # Aggregate per (Mark, side, horizon) — IGNORE which symbol; we want
    # mark-level skill regardless of asset.
    print("\n" + "=" * 90)
    print("MARK-LEVEL alpha (averaged across all assets / days)")
    print("=" * 90)
    print(f"{'mark':<10}  {'side':<5}  {'Δt':>6}  {'N':>6}  {'mean α':>10}  {'sd':>8}  "
          f"{'t-stat':>8}  {'verdict':<14}")
    print("-" * 90)

    g = (df.groupby(["mark", "side", "dt"])
           .agg(N=("alpha", "size"), mean=("alpha", "mean"),
                sd=("alpha", "std"))
           .reset_index())
    g["t"] = g["mean"] / (g["sd"] / np.sqrt(g["N"]))
    g = g.sort_values(["mark", "side", "dt"])

    for r in g.itertuples():
        verdict = (
            "INFORMED ★" if r.t > 3 else
            "informed"   if r.t > 2 else
            "FADE ★"     if r.t < -3 else
            "fade"       if r.t < -2 else
            "noise"
        )
        print(f"{r.mark:<10}  {r.side:<5}  {int(r.dt):>6}  {r.N:>6}  "
              f"{r.mean:>10.3f}  {r.sd:>8.3f}  {r.t:>8.2f}  {verdict:<14}")

    # Per-(Mark, product, side) at the most actionable horizon (dt=500)
    print("\n" + "=" * 90)
    print("PER-PRODUCT alpha @ Δt=500 (only |t|>2 cells shown)")
    print("=" * 90)
    print(f"{'mark':<10}  {'product':<22}  {'side':<5}  {'N':>5}  {'mean α':>10}  "
          f"{'t-stat':>8}  {'verdict':<14}")
    print("-" * 90)

    sub = df[df["dt"] == 500]
    g2 = (sub.groupby(["mark", "sym", "side"])
             .agg(N=("alpha", "size"), mean=("alpha", "mean"),
                  sd=("alpha", "std"))
             .reset_index())
    g2["t"] = g2["mean"] / (g2["sd"] / np.sqrt(g2["N"].clip(lower=2)))
    g2 = g2[(g2["N"] >= 30) & (g2["t"].abs() > 2)]
    g2 = g2.sort_values("t", ascending=False)
    for r in g2.itertuples():
        verdict = (
            "INFORMED ★" if r.t > 3 else
            "informed"   if r.t > 2 else
            "FADE ★"     if r.t < -3 else
            "fade"
        )
        print(f"{r.mark:<10}  {r.sym:<22}  {r.side:<5}  {r.N:>5}  "
              f"{r.mean:>10.3f}  {r.t:>8.2f}  {verdict:<14}")


if __name__ == "__main__":
    main()
