"""
Round 3 price-dynamics analysis.

For each product across all 3 days, compute:
  - Lag-1 autocorrelation of tick-to-tick returns
  - Variance ratio at lag 5 and 20 (Lo-MacKinlay)
  - Hurst exponent (R/S analysis)
  - Augmented Dickey-Fuller stationarity test
  - Half-life of mean reversion (AR(1) fit)
  - Total drift over the period

Verdict heuristic:
  - random walk:      |AR1| ~ 0,  H ~ 0.5,  VR ~ 1,  not stationary
  - mean reverting:   AR1 < 0,    H < 0.5,  VR < 1,  stationary
  - trending:         AR1 > 0,    H > 0.5,  VR > 1
"""

import csv
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from statsmodels.tsa.stattools import adfuller

REPO = Path(__file__).resolve().parent.parent
DATASETS = REPO / "backtester" / "datasets" / "round3"


def load_mid_series(product: str) -> np.ndarray:
    rows = []
    for day in (0, 1, 2):
        path = DATASETS / f"prices_round_3_day_{day}.csv"
        with path.open() as f:
            for r in csv.DictReader(f, delimiter=";"):
                if r["product"] == product and r["mid_price"]:
                    rows.append((int(r["timestamp"]) + day * 1_000_000, float(r["mid_price"])))
    rows.sort()
    return np.array([m for _, m in rows])


def lag1_autocorr(returns: np.ndarray) -> float:
    if len(returns) < 2:
        return 0.0
    a, b = returns[:-1], returns[1:]
    if a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def variance_ratio(returns: np.ndarray, k: int) -> float:
    if len(returns) < k + 1:
        return float("nan")
    var_1 = returns.var(ddof=1)
    if var_1 == 0:
        return float("nan")
    cum = np.cumsum(np.insert(returns, 0, 0))
    k_returns = cum[k:] - cum[:-k]
    var_k = k_returns.var(ddof=1)
    return float(var_k / (k * var_1))


def hurst_exponent(series: np.ndarray) -> float:
    """R/S analysis. H ~ 0.5 is random walk, < 0.5 mean reverting, > 0.5 trending."""
    series = np.asarray(series, dtype=float)
    if len(series) < 64:
        return float("nan")
    lags = [4, 8, 16, 32, 64, 128, 256]
    lags = [l for l in lags if l < len(series) // 2]
    rs = []
    for lag in lags:
        chunks = len(series) // lag
        rs_vals = []
        for i in range(chunks):
            seg = series[i * lag:(i + 1) * lag]
            mean = seg.mean()
            cumdev = np.cumsum(seg - mean)
            r = cumdev.max() - cumdev.min()
            s = seg.std(ddof=1)
            if s > 0:
                rs_vals.append(r / s)
        if rs_vals:
            rs.append(np.mean(rs_vals))
    if len(rs) < 3:
        return float("nan")
    log_lags = np.log(lags[: len(rs)])
    log_rs = np.log(rs)
    slope, _ = np.polyfit(log_lags, log_rs, 1)
    return float(slope)


def half_life(returns: np.ndarray, prices: np.ndarray) -> float:
    """Fit dY = a + b*Y_{-1} + e. half-life = -ln(2)/ln(1+b). NaN if b >= 0."""
    if len(prices) < 50:
        return float("nan")
    y = prices[1:] - prices[:-1]
    x = prices[:-1]
    x = np.column_stack([np.ones_like(x), x])
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    b = coef[1]
    if b >= 0:
        return float("nan")
    return float(-math.log(2) / math.log(1 + b))


def adf_pvalue(prices: np.ndarray) -> float:
    if len(prices) < 50 or prices.std() == 0:
        return float("nan")
    try:
        result = adfuller(prices, autolag="AIC")
        return float(result[1])
    except Exception:
        return float("nan")


def classify(ar1: float, vr5: float, hurst: float, adf_p: float) -> str:
    signals = []
    if not math.isnan(ar1):
        if ar1 < -0.05:
            signals.append("MR")
        elif ar1 > 0.05:
            signals.append("trend")
    if not math.isnan(vr5):
        if vr5 < 0.85:
            signals.append("MR")
        elif vr5 > 1.15:
            signals.append("trend")
    if not math.isnan(hurst):
        if hurst < 0.45:
            signals.append("MR")
        elif hurst > 0.55:
            signals.append("trend")
    if not math.isnan(adf_p) and adf_p < 0.05:
        signals.append("stationary")
    mr = signals.count("MR")
    trend = signals.count("trend")
    if mr >= 2:
        return "MEAN-REVERT"
    if trend >= 2:
        return "TREND"
    if "stationary" in signals and mr == 0 and trend == 0:
        return "stationary-noise"
    return "RANDOM-WALK"


def main():
    products = []
    for path in sorted(DATASETS.glob("prices_*.csv")):
        with path.open() as f:
            for r in csv.DictReader(f, delimiter=";"):
                products.append(r["product"])
        break
    products = sorted(set(products))

    cols = ("product", "n", "drift", "lag1_ac", "VR(5)", "VR(20)", "hurst", "adf_p", "halflife", "verdict")
    print(f"{cols[0]:<22} {cols[1]:>6} {cols[2]:>8} {cols[3]:>8} {cols[4]:>7} {cols[5]:>7} "
          f"{cols[6]:>7} {cols[7]:>7} {cols[8]:>10}  {cols[9]}")
    print("-" * 110)
    for product in products:
        prices = load_mid_series(product)
        if len(prices) < 100:
            continue
        returns = np.diff(prices)
        if returns.std() == 0:
            print(f"{product:<22} {len(prices):>6}  flat — no movement")
            continue
        drift = prices[-1] - prices[0]
        ar1 = lag1_autocorr(returns)
        vr5 = variance_ratio(returns, 5)
        vr20 = variance_ratio(returns, 20)
        h = hurst_exponent(prices)
        p = adf_pvalue(prices)
        hl = half_life(returns, prices)
        verdict = classify(ar1, vr5, h, p)

        def fmt(x, w):
            if isinstance(x, float) and math.isnan(x):
                return f"{'-':>{w}}"
            return f"{x:>{w}.3f}" if isinstance(x, float) else f"{x:>{w}}"

        print(f"{product:<22} {len(prices):>6} {fmt(drift, 8)} {fmt(ar1, 8)} "
              f"{fmt(vr5, 7)} {fmt(vr20, 7)} {fmt(h, 7)} {fmt(p, 7)} {fmt(hl, 10)}  {verdict}")


if __name__ == "__main__":
    main()
