"""
Per-voucher real-time decision data for VEV chain (round 4 default).

Produces the data points an option-trading algo needs as input:

  - bid / ask / mid + spread (from prices CSV, last tick of last day)
  - per-day traded volume (from trades CSV)
  - implied volatility (BS-inverted from market mid)
  - delta / theta / vega (BS Greeks, no rates/carry)
  - IV "inflation flag" (current IV vs rolling 5000-tick mean)

Set ROUND/DAYS/TTE_DAYS env vars to retarget; defaults: round 4, days
1-3, TTE=4d at start of D1.
"""

import csv
import math
import os
import statistics
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROUND = int(os.environ.get("ROUND", "4"))
DAYS = [int(x) for x in os.environ.get("DAYS", "1,2,3").split(",")]
TTE_AT_FIRST_DAY = float(os.environ.get("TTE_DAYS", "4"))
DATA = REPO / "backtester" / "datasets" / f"round{ROUND}"
CSV_OUT = REPO / "notebooks" / f"round{ROUND}_voucher_ticks.csv"

VEV_STRIKES = {
    "VEV_4000": 4000, "VEV_4500": 4500, "VEV_5000": 5000,
    "VEV_5100": 5100, "VEV_5200": 5200, "VEV_5300": 5300,
    "VEV_5400": 5400, "VEV_5500": 5500,
    "VEV_6000": 6000, "VEV_6500": 6500,
}
SPOT = "VELVETFRUIT_EXTRACT"
TICKS_PER_DAY = 10_000


def n_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def n_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S, K, T, sigma):
    if sigma <= 1e-9 or T <= 0:
        return max(S - K, 0.0)
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sq)
    return S * n_cdf(d1) - K * n_cdf(d1 - sigma * sq)


def implied_vol(price, S, K, T, lo=0.001, hi=2.5):
    if T <= 0 or price <= max(S - K, 0.0) + 1e-6:
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if bs_call(S, K, T, mid) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def greeks(S, K, T, sigma):
    """Returns (delta, gamma, theta_per_day, vega_per_pct_vol)."""
    if sigma <= 1e-9 or T <= 0:
        intrinsic = 1.0 if S > K else 0.0
        return intrinsic, 0.0, 0.0, 0.0
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sq)
    delta = n_cdf(d1)
    gamma = n_pdf(d1) / (S * sigma * sq)
    theta_per_year = -S * n_pdf(d1) * sigma / (2.0 * sq)
    theta_per_day = theta_per_year / 365.0
    vega_per_unit_vol = S * n_pdf(d1) * sq
    vega_per_pct = vega_per_unit_vol / 100.0
    return delta, gamma, theta_per_day, vega_per_pct


def t_for(d, tick_in_day):
    slot = DAYS.index(d)
    return (TTE_AT_FIRST_DAY - slot - tick_in_day / TICKS_PER_DAY) / 365.0


def load_prices():
    rows = defaultdict(list)
    for d in DAYS:
        for r in csv.DictReader((DATA / f"prices_round_{ROUND}_day_{d}.csv").open(),
                                 delimiter=";"):
            sym = r["product"]
            if sym not in VEV_STRIKES and sym != SPOT:
                continue
            ts = int(r["timestamp"])
            try:
                bid = int(r["bid_price_1"]) if r["bid_price_1"] else None
                ask = int(r["ask_price_1"]) if r["ask_price_1"] else None
                bv = int(r["bid_volume_1"]) if r["bid_volume_1"] else 0
                av = int(r["ask_volume_1"]) if r["ask_volume_1"] else 0
                mid = float(r["mid_price"]) if r["mid_price"] else None
            except ValueError:
                continue
            rows[sym].append((d, ts, bid, ask, bv, av, mid))
    return rows


def load_volume():
    vol = defaultdict(lambda: defaultdict(int))   # vol[sym][day] = total qty
    for d in DAYS:
        for r in csv.DictReader((DATA / f"trades_round_{ROUND}_day_{d}.csv").open(),
                                 delimiter=";"):
            sym = r["symbol"]
            if sym in VEV_STRIKES or sym == SPOT:
                vol[sym][d] += int(r["quantity"])
    return vol


def main():
    prices = load_prices()
    volume = load_volume()

    spot_by_ts = {(d, ts): mid for d, ts, _, _, _, _, mid in prices[SPOT] if mid}

    # Snapshot at the LAST tick available (defaults to end of last day).
    last_d = DAYS[-1]
    last_ts = max(ts for d, ts, *_ in prices[SPOT] if d == last_d)
    S_now = spot_by_ts.get((last_d, last_ts))
    T_now = t_for(last_d, last_ts // 100)
    print("=" * 90)
    print(f"VEV chain snapshot at end of round-{ROUND} D{last_d} (ts={last_ts})")
    print(f"  Spot S = VFRUIT mid = {S_now:.2f}")
    print(f"  TTE   = {T_now * 365:.3f} days  ({T_now:.5f} yrs)")
    print("=" * 90)
    hdr = f"{'voucher':<10}{'K':>5}{'bid':>6}{'ask':>6}{'mid':>9}{'spr':>4}" \
          f"{'vol_D'+str(last_d):>10}{'IV':>8}{'Δ':>7}{'θ/d':>9}{'Vega/1%':>10}{'IVinfl':>8}"
    print(hdr)
    print("-" * 90)

    # Pre-compute IV time series for the inflation flag (mean over the last
    # 5000 ticks of the chosen day).
    iv_window = defaultdict(list)
    for sym, K in VEV_STRIKES.items():
        for d, ts, _, _, _, _, mid in prices.get(sym, []):
            if d != last_d or mid is None:
                continue
            S = spot_by_ts.get((d, ts))
            if not S:
                continue
            T = t_for(d, ts // 100)
            iv = implied_vol(mid, S, K, T)
            if iv is not None:
                iv_window[sym].append((ts, iv))

    for sym, K in VEV_STRIKES.items():
        snap = next((row for row in reversed(prices.get(sym, []))
                     if row[0] == last_d and row[1] == last_ts), None)
        if not snap:
            continue
        d, ts, bid, ask, bv, av, mid = snap
        spr = (ask - bid) if (bid is not None and ask is not None) else None
        iv = implied_vol(mid, S_now, K, T_now) if mid else None
        if iv is None:
            iv_disp = "—"
            d_, g_, th_, vg_ = (1.0, 0.0, 0.0, 0.0) if S_now > K else (0.0, 0.0, 0.0, 0.0)
        else:
            iv_disp = f"{iv:.3f}"
            d_, g_, th_, vg_ = greeks(S_now, K, T_now, iv)
        # IV inflation: is current IV > 5000-tick mean of same-day IV?
        infl = "—"
        ts_iv = iv_window.get(sym, [])
        if ts_iv and iv is not None:
            recent = [v for t, v in ts_iv if t >= last_ts - 500_000]
            if len(recent) >= 50:
                mean_iv = statistics.mean(recent)
                pct = 100 * (iv - mean_iv) / mean_iv if mean_iv > 1e-6 else 0
                infl = f"{pct:+.0f}%"
        spr_s = f"{spr}" if spr is not None else "—"
        bid_s = f"{bid}" if bid is not None else "—"
        ask_s = f"{ask}" if ask is not None else "—"
        mid_s = f"{mid:.1f}" if mid is not None else "—"
        print(f"{sym:<10}{K:>5}{bid_s:>6}{ask_s:>6}{mid_s:>9}{spr_s:>4}"
              f"{volume[sym][last_d]:>10}{iv_disp:>8}"
              f"{d_:>7.3f}{th_:>9.2f}{vg_:>10.2f}{infl:>8}")

    # Per-day IV summary across the chain
    print()
    print("=" * 90)
    print(f"Per-day average IV (active strikes 5000-5500) — IV inflation overview")
    print("=" * 90)
    print(f"{'day':>4}{'mean IV':>10}{'min IV':>10}{'max IV':>10}{'sd':>9}{'N':>10}")
    for d in DAYS:
        ivs = []
        for sym, K in VEV_STRIKES.items():
            if K not in (5000, 5100, 5200, 5300, 5400, 5500):
                continue
            for dd, ts, _, _, _, _, mid in prices.get(sym, []):
                if dd != d or mid is None:
                    continue
                S = spot_by_ts.get((dd, ts))
                if not S:
                    continue
                T = t_for(dd, ts // 100)
                iv = implied_vol(mid, S, K, T)
                if iv is not None:
                    ivs.append(iv)
        if ivs:
            print(f"{d:>4}{statistics.mean(ivs):>10.4f}{min(ivs):>10.4f}"
                  f"{max(ivs):>10.4f}{statistics.pstdev(ivs):>9.4f}{len(ivs):>10}")

    # Volume summary per voucher per day
    print()
    print("=" * 90)
    print(f"Per-voucher per-day traded volume (liquidity check)")
    print("=" * 90)
    hdr_v = f"{'voucher':<12}" + "".join(f"{'D'+str(d):>10}" for d in DAYS) + f"{'TOTAL':>10}"
    print(hdr_v)
    for sym in VEV_STRIKES:
        cells = "".join(f"{volume[sym][d]:>10}" for d in DAYS)
        total = sum(volume[sym][d] for d in DAYS)
        print(f"{sym:<12}{cells}{total:>10}")
    sym = SPOT
    cells = "".join(f"{volume[sym][d]:>10}" for d in DAYS)
    total = sum(volume[sym][d] for d in DAYS)
    print(f"{sym:<12}{cells}{total:>10}")

    # ---- Per-tick CSV dump for downstream analysis ----
    # One row per (day, ts, voucher). Spot is also written so the consumer
    # can join. Greeks are only computed when IV inversion succeeds.
    n_rows = 0
    with CSV_OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "day", "ts", "product", "K", "bid", "ask", "bid_vol", "ask_vol",
            "mid", "spread", "S", "T_years", "intrinsic", "time_value",
            "iv", "delta", "gamma", "theta_per_day", "vega_per_pct",
        ])
        # Spot rows first (K blank).
        for d, ts, bid, ask, bv, av, mid in prices.get(SPOT, []):
            spr = (ask - bid) if (bid is not None and ask is not None) else ""
            w.writerow([d, ts, SPOT, "", bid or "", ask or "", bv, av,
                        mid if mid is not None else "", spr,
                        mid if mid is not None else "",
                        "", "", "", "", "", "", "", ""])
            n_rows += 1
        # Vouchers.
        for sym, K in VEV_STRIKES.items():
            for d, ts, bid, ask, bv, av, mid in prices.get(sym, []):
                S = spot_by_ts.get((d, ts))
                T = t_for(d, ts // 100)
                spr = (ask - bid) if (bid is not None and ask is not None) else ""
                if S is None or mid is None:
                    intr = tv = iv = ""
                    delta = gamma = theta = vega = ""
                else:
                    intr = max(0.0, S - K)
                    tv = mid - intr
                    iv_v = implied_vol(mid, S, K, T) if T > 0 else None
                    if iv_v is None:
                        iv = ""
                        # At/past expiry deep-ITM: greeks degenerate to (1,0,0,0)
                        if T <= 0 and S > K:
                            delta, gamma, theta, vega = 1.0, 0.0, 0.0, 0.0
                        else:
                            delta = gamma = theta = vega = ""
                    else:
                        iv = f"{iv_v:.6f}"
                        d_, g_, th_, vg_ = greeks(S, K, T, iv_v)
                        delta = f"{d_:.6f}"
                        gamma = f"{g_:.8f}"
                        theta = f"{th_:.4f}"
                        vega = f"{vg_:.4f}"
                w.writerow([
                    d, ts, sym, K,
                    bid if bid is not None else "",
                    ask if ask is not None else "",
                    bv, av,
                    mid if mid is not None else "",
                    spr,
                    f"{S:.2f}" if S is not None else "",
                    f"{T:.6f}",
                    f"{intr:.2f}" if intr != "" else "",
                    f"{tv:.4f}" if tv != "" else "",
                    iv, delta, gamma, theta, vega,
                ])
                n_rows += 1
    print()
    print(f"wrote {CSV_OUT}  ({n_rows:,} rows, {CSV_OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
