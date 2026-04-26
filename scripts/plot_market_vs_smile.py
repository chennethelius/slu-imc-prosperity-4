"""
Compare market mids of all 10 VEV vouchers vs Black-Scholes fair priced at
the *smile IV* — i.e., we fit IV = a·m² + b·m + c (active strikes 5000–5500
pooled across 3 days), then for every voucher at every tick we evaluate
fair_px = BS(S, K, T, smile_IV(m)).

Outputs:
  - notebooks/round3_market_vs_smile.png  (time-series of market−fair per voucher)
  - printed summary table per strike
"""

import csv
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "backtester" / "datasets" / "round3"
OUT = REPO / "notebooks" / "round3_market_vs_smile.png"

VEV_STRIKES = {
    "VEV_4000": 4000, "VEV_4500": 4500, "VEV_5000": 5000,
    "VEV_5100": 5100, "VEV_5200": 5200, "VEV_5300": 5300,
    "VEV_5400": 5400, "VEV_5500": 5500,
    "VEV_6000": 6000, "VEV_6500": 6500,
}
FIT_STRIKES = {5000, 5100, 5200, 5300, 5400, 5500}
SPOT = "VELVETFRUIT_EXTRACT"
T_DAYS_AT_DAY_0 = 5
TICKS_PER_DAY = 10_000


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S, K, T, sigma):
    if sigma <= 1e-9 or T <= 0:
        return max(S - K, 0.0)
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sq)
    return S * norm_cdf(d1) - K * norm_cdf(d1 - sigma * sq)


def implied_vol(price, S, K, T, lo=0.001, hi=2.0):
    if price <= max(S - K, 0.0) + 1e-6 or T <= 0:
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if bs_call(S, K, T, mid) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def load_mids():
    out = {p: [] for p in list(VEV_STRIKES) + [SPOT]}
    for d in (0, 1, 2):
        with (DATA / f"prices_round_3_day_{d}.csv").open() as f:
            for r in csv.DictReader(f, delimiter=";"):
                if r["product"] not in out or not r["mid_price"]:
                    continue
                ts = int(r["timestamp"])
                gi = d * TICKS_PER_DAY + ts // 100
                out[r["product"]].append((gi, d, ts, float(r["mid_price"])))
    return out


def main():
    mids = load_mids()
    spot_by = {(d, ts): m for _, d, ts, m in mids[SPOT]}

    # 1) Fit smile on active strikes pooled across 3 days.
    pool = []
    for prod, K in VEV_STRIKES.items():
        if K not in FIT_STRIKES:
            continue
        for gi, d, ts, mid in mids[prod]:
            S = spot_by.get((d, ts))
            if S is None:
                continue
            T = (T_DAYS_AT_DAY_0 - d - (ts / 100) / TICKS_PER_DAY) / 365.0
            if T <= 0:
                continue
            iv = implied_vol(mid, S, K, T)
            if iv is None:
                continue
            pool.append((math.log(K / S) / math.sqrt(T), iv))
    ms = np.array([p[0] for p in pool])
    ivs = np.array([p[1] for p in pool])
    a, b, c = np.polyfit(ms, ivs, 2)
    print(f"Smile fit (active-only pool): IV = {a:+.4f}·m² {b:+.4f}·m + {c:.4f}")
    print(f"  N={len(pool):,}  residual_sd={(ivs - (a*ms*ms + b*ms + c)).std():.4f}")
    print()

    # 2) For every voucher, every tick, compute BS-smile fair vs market.
    diffs = {p: [] for p in VEV_STRIKES}  # (gi, market, fair, diff)
    for prod, K in VEV_STRIKES.items():
        for gi, d, ts, mid in mids[prod]:
            S = spot_by.get((d, ts))
            if S is None:
                continue
            T = (T_DAYS_AT_DAY_0 - d - (ts / 100) / TICKS_PER_DAY) / 365.0
            if T <= 0:
                continue
            m = math.log(K / S) / math.sqrt(T)
            smile_iv = max(0.05, a * m * m + b * m + c)
            fair = bs_call(S, K, T, smile_iv)
            diffs[prod].append((gi, mid, fair, mid - fair))

    # 3) Summary table.
    print(f"{'voucher':>10}  {'avg mkt':>9}  {'avg fair':>9}  "
          f"{'avg diff':>9}  {'sd diff':>9}  {'%mispriced':>11}")
    for prod, K in VEV_STRIKES.items():
        d = diffs[prod]
        if not d:
            continue
        mkt = np.array([x[1] for x in d])
        fair = np.array([x[2] for x in d])
        diff = np.array([x[3] for x in d])
        mispc = 100.0 * np.mean(np.abs(diff) > 1.0)
        print(f"{prod:>10}  {mkt.mean():>9.2f}  {fair.mean():>9.2f}  "
              f"{diff.mean():>+9.2f}  {diff.std():>9.2f}  "
              f"{mispc:>10.1f}%")

    # 4) Plot diff time series.
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
    cmap = plt.cm.tab10
    for i, (prod, K) in enumerate(VEV_STRIKES.items()):
        d = diffs[prod]
        if not d:
            continue
        gi = [x[0] for x in d]
        diff = [x[3] for x in d]
        ax = axes[0] if K in FIT_STRIKES else axes[1]
        ax.plot(gi, diff, lw=0.5, alpha=0.8, color=cmap(i % 10),
                label=f"{prod} (K={K})")
    for ax, ttl in zip(axes, ["Active strikes (5000-5500) used for fit",
                              "Wing strikes (4000/4500/6000/6500)"]):
        ax.axhline(0, color="black", lw=0.5)
        ax.set_ylabel("market − BS(smile IV)")
        ax.legend(fontsize=7, loc="upper right", ncol=2)
        ax.grid(alpha=0.25)
        ax.set_title(ttl, fontsize=10)
        for d_end in (TICKS_PER_DAY, 2 * TICKS_PER_DAY):
            ax.axvline(d_end, color="black", lw=0.4, alpha=0.4)
    axes[1].set_xlabel("global tick (D0 → D1 → D2)")
    fig.suptitle("Market mid vs BS fair (using fitted smile IV)", fontsize=12)
    plt.tight_layout()
    plt.savefig(OUT, dpi=140, bbox_inches="tight")
    print(f"\nsaved {OUT}")


if __name__ == "__main__":
    main()
