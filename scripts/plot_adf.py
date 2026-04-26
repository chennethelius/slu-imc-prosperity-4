"""
Rolling ADF (Augmented Dickey-Fuller) plot for HYDROGEL_PACK and
VELVETFRUIT_EXTRACT mid-prices across round 3's 3 days.

ADF regresses  ΔY_t = α + β·Y_{t-1} + Σ γ_i·ΔY_{t-i} + ε  and returns the
t-stat on β. Under the null hypothesis of a unit root (random walk), β=0.
Strongly negative t-stats → reject unit root → series is mean-reverting.

Critical values (constant, no trend):
  5%: -2.86,  1%: -3.43

Set ROUND/DAYS env vars to retarget.
Output: notebooks/round{ROUND}_adf.png
"""

import csv
import math
import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).resolve().parent.parent
ROUND = int(os.environ.get("ROUND", "3"))
DAYS = [int(x) for x in os.environ.get("DAYS", "0,1,2").split(",")]
DATA = REPO / "backtester" / "datasets" / f"round{ROUND}"
OUT = REPO / "notebooks" / f"round{ROUND}_adf.png"

PRODUCTS = ["HYDROGEL_PACK", "VELVETFRUIT_EXTRACT"]
WINDOW = 1000   # ticks per ADF window
STRIDE = 100    # how often to re-fit
LAGS = 2        # augmentation lags


def load_mid(product):
    out = []
    for slot, d in enumerate(DAYS):
        with (DATA / f"prices_round_{ROUND}_day_{d}.csv").open() as f:
            for r in csv.DictReader(f, delimiter=";"):
                if r["product"] != product or not r["mid_price"]:
                    continue
                ts = int(r["timestamp"])
                gi = slot * 10000 + ts // 100
                out.append((gi, float(r["mid_price"])))
    return out


def adf_stat(y, p=2):
    y = np.asarray(y, dtype=float)
    n = len(y)
    rows = n - p - 1
    if rows < 5:
        return None
    dy = np.diff(y)                # length n-1
    X = np.ones((rows, 2 + p))
    Y = np.empty(rows)
    for i in range(rows):
        Y[i] = dy[i + p]
        X[i, 1] = y[i + p]
        for j in range(p):
            X[i, 2 + j] = dy[i + p - 1 - j]
    try:
        beta, *_ = np.linalg.lstsq(X, Y, rcond=None)
    except np.linalg.LinAlgError:
        return None
    err = Y - X @ beta
    dof = rows - X.shape[1]
    if dof <= 0:
        return None
    sigma2 = (err * err).sum() / dof
    try:
        cov = np.linalg.pinv(X.T @ X) * sigma2
    except np.linalg.LinAlgError:
        return None
    se = math.sqrt(max(0.0, cov[1, 1]))
    return beta[1] / se if se > 1e-12 else None


def rolling_adf(series, window, stride, p):
    idxs = [t for t, _ in series]
    vals = [v for _, v in series]
    out = []
    for start in range(0, len(series) - window + 1, stride):
        y = vals[start : start + window]
        a = adf_stat(y, p=p)
        if a is not None:
            out.append((idxs[start + window - 1], a))
    return out


def main():
    fig, axes = plt.subplots(len(PRODUCTS), 1, figsize=(13, 4 * len(PRODUCTS)),
                             sharex=True)
    if len(PRODUCTS) == 1:
        axes = [axes]
    for ax, prod in zip(axes, PRODUCTS):
        s = load_mid(prod)
        if not s:
            print(f"no data for {prod}")
            continue
        adfs = rolling_adf(s, WINDOW, STRIDE, LAGS)
        xs = [a[0] for a in adfs]
        ys = [a[1] for a in adfs]
        ax.plot(xs, ys, lw=0.9, color="steelblue")
        ax.axhline(-2.86, color="orange", lw=0.7, ls="--",
                   label="5% critical (−2.86)")
        ax.axhline(-3.43, color="red", lw=0.7, ls="--",
                   label="1% critical (−3.43)")
        ax.axhline(0, color="black", lw=0.3, alpha=0.5)
        for k in range(1, len(DAYS)):
            ax.axvline(k * 10000, color="black", lw=0.4, alpha=0.4)
        ax.set_ylabel(f"{prod}\nADF t-stat")
        ax.legend(fontsize=8, loc="lower right")
        ax.grid(alpha=0.25)
        below5 = sum(1 for v in ys if v < -2.86)
        below1 = sum(1 for v in ys if v < -3.43)
        print(f"{prod}:  N={len(ys)}  median={np.median(ys):+.2f}  "
              f"min={min(ys):+.2f}  max={max(ys):+.2f}  "
              f"below 5%: {below5}/{len(ys)} ({100*below5/len(ys):.0f}%)  "
              f"below 1%: {below1}/{len(ys)} ({100*below1/len(ys):.0f}%)")
    axes[-1].set_xlabel(f"global tick ({' → '.join(f'D{d}' for d in DAYS)})")
    fig.suptitle(f"Rolling ADF — window={WINDOW}, stride={STRIDE}, lags={LAGS}",
                 fontsize=12)
    plt.tight_layout()
    plt.savefig(OUT, dpi=140, bbox_inches="tight")
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()
