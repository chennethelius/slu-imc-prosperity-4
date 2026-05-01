"""Round 5 — statistical analysis of the untouched OXYGEN_SHAKE group.

Question: does OXYGEN_SHAKE actually have NO edge, or did the prior analysis
miss something? The brief says "in certain groups, strong patterns are
embedded in the price movements." OXYGEN_SHAKE is the only group with all 5
products skipped — worth a careful look.

Tests:
  1. Per-product basic stats: range, vol, drift
  2. Hurst exponent (R/S analysis)
  3. Return autocorrelation at lags 1, 5, 25
  4. ADF stationarity test (proxy via lag-1 mean reversion)
  5. Within-group cointegration (Engle-Granger on all 10 pairs)
  6. Spread half-life (Ornstein-Uhlenbeck fit on residuals)
  7. Cross-correlation matrix
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
with open(REPO / ".github" / "round5_data.json") as f:
    DATA = json.load(f)

GROUP = "OXYGEN_SHAKE"
PRODUCTS = [f"{GROUP}_{s}" for s in DATA["groups"][GROUP]]
DAYS = ["2", "3", "4"]


# ── Build a clean wide DataFrame: one row per timestamp, columns per product
def load_wide() -> pd.DataFrame:
    """Stitch days 2,3,4 mids into one continuous series per product."""
    frames = []
    for d in DAYS:
        per_prod = {}
        for sym in PRODUCTS:
            mids = DATA["products"][sym][d]["mids"]
            ser = pd.Series({ts: px for ts, px in mids}, name=sym)
            per_prod[sym] = ser
        df = pd.concat(per_prod.values(), axis=1, keys=per_prod.keys())
        df.index = df.index + 1_000_000 * (int(d) - 2)  # offset days
        frames.append(df)
    out = pd.concat(frames).sort_index()
    out = out.ffill().dropna()
    return out


df = load_wide()
print(f"Loaded {len(df)} timestamps × {len(df.columns)} products from days {DAYS}")
print(df.describe().round(2).T[["mean", "std", "min", "max"]])

# ── 1. Per-product summary
print("\n=== Per-product return stats (log-returns from decimated series) ===")
ret = np.log(df).diff().dropna()
summary = pd.DataFrame({
    "mean_ret_bp": ret.mean() * 1e4,
    "std_ret_bp":  ret.std() * 1e4,
    "n_obs":       ret.count(),
    "annvol_pct":  ret.std() * np.sqrt(252 * 4) * 100,  # 4 obs/day decimated
})
print(summary.round(3))

# ── 2. Hurst exponent (R/S analysis)
def hurst_rs(x: np.ndarray) -> float:
    """Hurst via R/S analysis. H<0.5 mean-revert, ≈0.5 random walk, >0.5 trend."""
    x = np.asarray(x)
    n = len(x)
    lags = [int(n / k) for k in (32, 16, 8, 4, 2)]
    rs_vals = []
    for lag in lags:
        if lag < 8: continue
        chunks = n // lag
        rs_chunk = []
        for i in range(chunks):
            seg = x[i*lag:(i+1)*lag]
            if len(seg) < 4: continue
            mean = seg.mean()
            dev  = seg - mean
            cum  = dev.cumsum()
            r    = cum.max() - cum.min()
            s    = seg.std(ddof=1)
            if s > 0:
                rs_chunk.append(r / s)
        if rs_chunk:
            rs_vals.append((lag, np.mean(rs_chunk)))
    if len(rs_vals) < 2:
        return float("nan")
    log_lag = np.log([l for l, _ in rs_vals])
    log_rs  = np.log([r for _, r in rs_vals])
    return float(np.polyfit(log_lag, log_rs, 1)[0])

print("\n=== Hurst exponent (R/S) ===")
print("H<0.5 = mean-revert, ~0.5 = random walk, >0.5 = trending")
hursts = {sym: hurst_rs(df[sym].values) for sym in PRODUCTS}
for sym, h in hursts.items():
    flag = "MEAN-REVERT" if h < 0.45 else "TREND" if h > 0.55 else "random walk"
    print(f"  {sym:30s} H = {h:.3f}  ({flag})")

# ── 3. Return autocorrelation at lags 1, 5, 25
print("\n=== Return autocorrelation (negative => mean-reversion at that lag) ===")
ac = pd.DataFrame({
    sym: [ret[sym].autocorr(lag=k) for k in (1, 5, 25)]
    for sym in PRODUCTS
}, index=["lag1", "lag5", "lag25"]).T
print(ac.round(3))

# ── 4. Mean-reversion proxy: regress Δp_t on p_{t-1} - mean
print("\n=== Mean-reversion proxy (β coefficient: more negative = faster MR) ===")
# β > 0 means trending, β < 0 means mean-reverting, β ≈ 0 means random walk
mr_results = {}
for sym in PRODUCTS:
    p = df[sym].values
    dp = np.diff(p)
    p_lag = p[:-1] - p[:-1].mean()
    if p_lag.var() > 0:
        beta = np.cov(dp, p_lag, ddof=1)[0, 1] / p_lag.var()
        # Half-life of mean reversion (in obs) if β < 0
        hl = -np.log(2) / np.log(1 + beta) if -1 < beta < 0 else np.inf
        mr_results[sym] = (beta, hl)
    else:
        mr_results[sym] = (np.nan, np.inf)
for sym, (b, hl) in mr_results.items():
    flag = f"HL={hl:.0f} obs" if hl < 500 else "no MR"
    print(f"  {sym:30s} β = {b:+.5f}  ({flag})")

# ── 5. Within-group cointegration: Engle-Granger style
# For each pair (a, b): regress a = α + β·b, then test residuals for stationarity
# via lag-1 AR coefficient. AR coef << 1 = stationary residuals = cointegrated.
print("\n=== Within-group cointegration (10 pairs) ===")
print("Residual lag-1 AR << 1 → cointegrated. Half-life of spread shown when MR.")
from itertools import combinations
coint_rows = []
for a, b in combinations(PRODUCTS, 2):
    pa, pb = df[a].values, df[b].values
    # Regress a on b: a = alpha + beta*b + eps
    beta = np.cov(pa, pb, ddof=1)[0, 1] / pb.var(ddof=1)
    alpha = pa.mean() - beta * pb.mean()
    resid = pa - alpha - beta * pb
    # Lag-1 AR on residuals
    r0, r1 = resid[:-1], resid[1:]
    ar1 = np.cov(r0, r1, ddof=1)[0, 1] / r0.var(ddof=1) if r0.var(ddof=1) > 0 else 1.0
    hl = -np.log(2) / np.log(ar1) if 0 < ar1 < 1 else np.inf
    resid_std = resid.std(ddof=1)
    coint_rows.append({
        "pair": f"{a.replace(GROUP+'_','')} ~ {b.replace(GROUP+'_','')}",
        "beta": round(beta, 3),
        "ar1": round(ar1, 4),
        "half_life": round(hl, 0) if hl < 1e4 else "—",
        "resid_std": round(resid_std, 2),
    })
coint_df = pd.DataFrame(coint_rows).sort_values("ar1")
print(coint_df.to_string(index=False))

# ── 6. Cross-product correlation matrix (returns)
print("\n=== Return correlation matrix (log-returns) ===")
print(ret.corr().round(3))

# ── 7. Spread profitability sketch: assume MM-style spread collection on the
# tightest cointegrating pair. Half-life × spread vol gives a rough edge bound.
print("\n=== Top 3 cointegrating pairs — potential spread MM ===")
top3 = coint_df.head(3)
print(top3.to_string(index=False))

# ── 8. Big-day check — was there a regime move on any specific day?
print("\n=== Per-day per-product range (max - min as % of day mean) ===")
day_range = pd.DataFrame(index=PRODUCTS, columns=DAYS)
for d in DAYS:
    dft = pd.DataFrame({
        sym: pd.Series({ts: px for ts, px in DATA["products"][sym][d]["mids"]})
        for sym in PRODUCTS
    })
    rng = (dft.max() - dft.min()) / dft.mean() * 100
    for sym in PRODUCTS: day_range.loc[sym, d] = round(rng[sym], 1)
print(day_range)
