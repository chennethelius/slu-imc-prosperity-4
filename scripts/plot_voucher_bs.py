"""
Black-Scholes diagnostic plot for the Velvetfruit Extract Vouchers.

For each tick of the configured days:
  S      = VELVETFRUIT_EXTRACT mid
  K_i    = strike of voucher i
  T      = (TTE_AT_FIRST_DAY - (d - first_day) - tick_in_day/10000) / 365
  IV_i   = invert BS_call(S, K_i, T, σ) = market_mid_i

Set ROUND/DAYS/TTE_DAYS env vars to retarget. TTE_DAYS = days-to-expiry
at the start of DAYS[0]. Round 3 default: 5d (vouchers expire 7d after
round 1 start). Round 4 default: 2d (continuation; deep-ITM TV ≈ 0 in
the data confirms vouchers are at/near expiry).

Output: notebooks/round{ROUND}_voucher_bs.png
"""

import csv
import math
import os
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).resolve().parent.parent
ROUND = int(os.environ.get("ROUND", "3"))
DAYS = [int(x) for x in os.environ.get("DAYS", "0,1,2").split(",")]
DATA = REPO / "backtester" / "datasets" / f"round{ROUND}"
OUT = REPO / "notebooks" / f"round{ROUND}_voucher_bs.png"

VEV_STRIKES = {
    "VEV_4000": 4000, "VEV_4500": 4500, "VEV_5000": 5000,
    "VEV_5100": 5100, "VEV_5200": 5200, "VEV_5300": 5300,
    "VEV_5400": 5400, "VEV_5500": 5500,
    "VEV_6000": 6000, "VEV_6500": 6500,
}
SPOT = "VELVETFRUIT_EXTRACT"
# Days to expiry at the start of DAYS[0]. Round 3 default 5; round 4 default 2.
TTE_AT_FIRST_DAY = float(os.environ.get("TTE_DAYS", "4" if ROUND == 4 else "5"))
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
    """Returns dict[product] -> list[(global_tick_idx, day, ts, mid)]."""
    out = {p: [] for p in list(VEV_STRIKES) + [SPOT]}
    for slot, d in enumerate(DAYS):
        with (DATA / f"prices_round_{ROUND}_day_{d}.csv").open() as f:
            for r in csv.DictReader(f, delimiter=";"):
                if r["product"] not in out or not r["mid_price"]:
                    continue
                ts = int(r["timestamp"])
                global_idx = slot * TICKS_PER_DAY + ts // 100
                out[r["product"]].append((global_idx, d, ts, float(r["mid_price"])))
    return out


def t_for(d, tick_in_day):
    """Days-to-expiry → years, given absolute day d in DAYS list."""
    slot = DAYS.index(d)
    return (TTE_AT_FIRST_DAY - slot - tick_in_day / TICKS_PER_DAY) / 365.0


def main():
    mids = load_mids()

    # Index spot by (day, ts)
    spot_by = {(d, ts): m for _, d, ts, m in mids[SPOT]}

    # Per-voucher IV time series
    iv_series = {p: [] for p in VEV_STRIKES}      # (global_idx, IV)
    fair_series = {p: [] for p in VEV_STRIKES}    # (global_idx, BS_fair_using_atm_iv)
    market_series = {p: [] for p in VEV_STRIKES}  # (global_idx, market_mid)
    pooled = []  # (m_standardized, iv, K) for the smile scatter
    for prod, K in VEV_STRIKES.items():
        for gi, d, ts, mid in mids[prod]:
            S = spot_by.get((d, ts))
            if S is None:
                continue
            tick_in_day = ts // 100
            T = t_for(d, tick_in_day)
            if T <= 0:
                continue
            iv = implied_vol(mid, S, K, T)
            if iv is not None:
                iv_series[prod].append((gi, iv))
                pooled.append((math.log(K / S) / math.sqrt(T), iv, K))
            market_series[prod].append((gi, mid))

    # Snapshots: start of first day, end of first day, mid of middle day, end of last day
    n = len(DAYS)
    snap_idx = [0, TICKS_PER_DAY - 1, int((n // 2 + 0.5) * TICKS_PER_DAY) if n > 1 else TICKS_PER_DAY // 2, n * TICKS_PER_DAY - 1]
    snap_labels = [f"D{DAYS[0]} t=0", f"D{DAYS[0]} t=99,900",
                   f"D{DAYS[min(n-1, n//2)]} t=mid",
                   f"D{DAYS[-1]} t=99,900"]
    snapshots = []
    for sidx in snap_idx:
        slot = sidx // TICKS_PER_DAY
        if slot >= n:
            continue
        d = DAYS[slot]
        ts = (sidx % TICKS_PER_DAY) * 100
        S = spot_by.get((d, ts))
        if S is None:
            continue
        T = t_for(d, ts // 100)
        rows = []
        for prod, K in VEV_STRIKES.items():
            mids_for_prod = dict(((g, m) for g, _, _, m in
                                  [(gi, dd, tt, mm) for gi, dd, tt, mm in mids[prod]
                                   if dd == d and tt == ts]))
            if not mids_for_prod:
                continue
            mid = list(mids_for_prod.values())[0]
            iv = implied_vol(mid, S, K, T)
            rows.append((prod, K, mid, iv, math.log(K / S)))
        snapshots.append((sidx, S, T, rows))

    # ------------------------------------------------------------- plot
    fig = plt.figure(figsize=(14, 10))
    gs = fig.add_gridspec(3, 1, height_ratios=[2, 2, 1.6], hspace=0.35)
    ax_smile = fig.add_subplot(gs[0])
    ax_iv = fig.add_subplot(gs[1])
    ax_tv = fig.add_subplot(gs[2])

    # Panel 1: pooled IV smile, x = ln(K/S)/√T, with quadratic fit
    strike_list = sorted(set(K for _, _, K in pooled))
    cmap_smile = plt.cm.coolwarm(np.linspace(0.0, 1.0, len(strike_list)))
    strike_color = dict(zip(strike_list, cmap_smile))
    for K in strike_list:
        pts = [(m, iv) for m, iv, kk in pooled if kk == K]
        if not pts:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax_smile.scatter(xs, ys, s=2, color=strike_color[K], alpha=0.35,
                         label=f"K={K}")
    ACTIVE = {5000, 5100, 5200, 5300, 5400, 5500}
    if len(pooled) >= 3:
        ms_all = np.array([p[0] for p in pooled])
        ivs_all = np.array([p[1] for p in pooled])
        a0, b0, c0 = np.polyfit(ms_all, ivs_all, 2)
        pred0 = a0 * ms_all * ms_all + b0 * ms_all + c0
        resid0 = ivs_all - pred0
        print(f"\nFULL-pool fit (incl. wings): "
              f"a={a0:.4f}  b={b0:.4f}  c={c0:.4f}  "
              f"residual_sd={resid0.std():.4f}  N={len(ms_all):,}")

        active = [(m, iv) for m, iv, K in pooled if K in ACTIVE]
        ms = np.array([p[0] for p in active])
        ivs = np.array([p[1] for p in active])
        a, b, c = np.polyfit(ms, ivs, 2)
        xx = np.linspace(ms.min(), ms.max(), 200)
        yy = a * xx * xx + b * xx + c
        ax_smile.plot(xx, yy, color="white", lw=1.6,
                      label=f"Active-only fit: {a:.3f}m² {b:+.4f}m + {c:.3f}")
        pred = a * ms * ms + b * ms + c
        resid = ivs - pred
        print(f"ACTIVE-only fit (5000-5500): "
              f"a={a:.4f}  b={b:.4f}  c={c:.4f}  "
              f"residual_sd={resid.std():.4f}  N={len(ms):,}")
        print(f"  fit IV span across active range: "
              f"{yy.max()-yy.min():.4f}  (peak {yy.max():.4f}, trough {yy.min():.4f})")
        snr = (yy.max() - yy.min()) / (2 * resid.std())
        print(f"  signal/noise ratio (curve span / 2σ_resid): {snr:.2f}")
    ax_smile.set_xlabel("standardized moneyness  m = ln(K/S) / √T")
    ax_smile.set_ylabel("implied volatility")
    ax_smile.set_title(
        f"Vol smile: pooled {len(pooled):,} quotes across {len(DAYS)} days, quadratic fit",
        fontsize=11,
    )
    ax_smile.legend(fontsize=7, loc="upper left", ncol=2,
                    markerscale=3, framealpha=0.9)
    ax_smile.grid(alpha=0.25)

    # Panel 2: IV time series per voucher
    cmap = plt.cm.tab10
    for i, (prod, K) in enumerate(VEV_STRIKES.items()):
        ser = iv_series[prod]
        if len(ser) < 100:
            continue
        xs = [g for g, _ in ser]
        ys = [iv for _, iv in ser]
        ax_iv.plot(xs, ys, lw=0.45, color=cmap(i % 10), alpha=0.8, label=f"{prod}")
    ax_iv.set_ylabel("implied volatility")
    ax_iv.set_title(f"IV per voucher over {len(DAYS)} days  (T={TTE_AT_FIRST_DAY}d at D{DAYS[0]} start, decreasing)", fontsize=11)
    ax_iv.legend(fontsize=7, loc="upper right", ncol=2)
    ax_iv.grid(alpha=0.25)
    for k in range(1, len(DAYS)):
        ax_iv.axvline(k * TICKS_PER_DAY, color="black", lw=0.4, alpha=0.4)
    ax_iv.set_ylim(0, 1.0)

    # Panel 3: time value (market - intrinsic) per voucher
    for i, (prod, K) in enumerate(VEV_STRIKES.items()):
        if not market_series[prod]:
            continue
        xs = []
        ys = []
        for gi, mid in market_series[prod]:
            slot = gi // TICKS_PER_DAY
            if slot >= len(DAYS): continue
            d = DAYS[slot]
            ts = (gi % TICKS_PER_DAY) * 100
            S = spot_by.get((d, ts))
            if S is None: continue
            tv = mid - max(S - K, 0)
            xs.append(gi)
            ys.append(tv)
        ax_tv.plot(xs, ys, lw=0.45, color=cmap(i % 10), alpha=0.8, label=prod)
    ax_tv.set_ylabel("time value (mid − intrinsic)")
    ax_tv.set_xlabel(f"tick index ({' → '.join(f'D{d}' for d in DAYS)})")
    ax_tv.set_title("Time value per voucher", fontsize=11)
    ax_tv.set_yscale("symlog", linthresh=1)
    ax_tv.legend(fontsize=7, loc="upper right", ncol=2)
    ax_tv.grid(alpha=0.25)
    for k in range(1, len(DAYS)):
        ax_tv.axvline(k * TICKS_PER_DAY, color="black", lw=0.4, alpha=0.4)

    plt.suptitle("Black-Scholes diagnostic: VEV vouchers vs VELVETFRUIT_EXTRACT spot", fontsize=12)
    plt.savefig(OUT, dpi=140, bbox_inches="tight")
    print(f"saved {OUT}")

    # Print a one-shot table summary
    print("\nIV snapshot at each panel-1 timestamp:")
    for (sidx, S, T, rows), label in zip(snapshots, snap_labels):
        print(f"\n  {label}  S={S:.2f}  T={T*365:.2f} days")
        print(f"  {'voucher':>10} {'K':>5} {'mid':>8} {'TV':>7} {'IV':>8}")
        for p, K, m, iv, ln in rows:
            tv = m - max(S - K, 0)
            iv_s = f"{iv:.4f}" if iv is not None else "—"
            print(f"  {p:>10} {K:>5} {m:>8.1f} {tv:>+7.2f} {iv_s:>8}")


if __name__ == "__main__":
    main()
