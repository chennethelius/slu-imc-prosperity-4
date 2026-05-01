"""Round 5 — cross-group cointegration scan across all 50 products.

The team's prior analysis tested 100 within-group pairs (10 per group × 10
groups) and found 0 cointegrating. They never tested cross-group pairs.
Total cross-group pairs = C(50,2) − within-group = 1225 − 100 = 1125.

If IMC's "strong patterns embedded" hint refers to cointegration, it would
most likely live cross-group, since within-group is the obvious test
everyone runs first.

For each pair (a, b):
  1. OLS regress a on b: a = α + β·b
  2. Compute residuals e_t = a_t − α − β·b_t
  3. Lag-1 AR coefficient on residuals (proxy for stationarity)
  4. Half-life = -ln(2) / ln(AR1) when 0 < AR1 < 1
  5. Residual standard deviation (must be wide enough relative to fees but
     mean-revert fast enough to harvest)
  6. Return correlation as side-evidence (high return-corr but low residual
     half-life is the gold standard for stat-arb)

Output: top pairs ranked by half-life, filtered to cross-group.
"""
import json
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
with open(REPO / ".github" / "round5_data.json") as f:
    DATA = json.load(f)

GROUPS = DATA["groups"]
DAYS = ("2", "3", "4")

ALL_PRODUCTS = []
SYM_TO_GROUP = {}
for group, suffixes in GROUPS.items():
    for s in suffixes:
        sym = f"{group}_{s}"
        ALL_PRODUCTS.append(sym)
        SYM_TO_GROUP[sym] = group


def load_wide() -> pd.DataFrame:
    """Stitch days 2,3,4 mids into one continuous series per product."""
    frames = []
    for d in DAYS:
        per_prod = {}
        for sym in ALL_PRODUCTS:
            mids = DATA["products"][sym][d]["mids"]
            per_prod[sym] = pd.Series({ts: px for ts, px in mids}, name=sym)
        df = pd.concat(per_prod.values(), axis=1, keys=per_prod.keys())
        df.index = df.index + 1_000_000 * (int(d) - 2)
        frames.append(df)
    out = pd.concat(frames).sort_index().ffill().dropna()
    return out


df = load_wide()
print(f"Loaded {len(df)} timestamps × {len(df.columns)} products")
ret = np.log(df).diff().dropna()


def coint_pair(pa: np.ndarray, pb: np.ndarray) -> dict:
    """OLS a~b, return half-life and residual stats."""
    if pb.var(ddof=1) == 0:
        return None
    beta = np.cov(pa, pb, ddof=1)[0, 1] / pb.var(ddof=1)
    alpha = pa.mean() - beta * pb.mean()
    e = pa - alpha - beta * pb
    e0, e1 = e[:-1], e[1:]
    if e0.var(ddof=1) == 0:
        return None
    ar1 = np.cov(e0, e1, ddof=1)[0, 1] / e0.var(ddof=1)
    if not (0 < ar1 < 1):
        hl = float("inf")
    else:
        hl = -np.log(2) / np.log(ar1)
    return {"beta": beta, "alpha": alpha, "ar1": ar1, "hl": hl,
            "resid_std": e.std(ddof=1), "resid_max": np.abs(e).max()}


# Scan all 1225 pairs
results = []
syms = ALL_PRODUCTS
for a, b in combinations(syms, 2):
    r = coint_pair(df[a].values, df[b].values)
    if r is None:
        continue
    return_corr = ret[a].corr(ret[b])
    results.append({
        "a": a, "b": b,
        "a_grp": SYM_TO_GROUP[a],
        "b_grp": SYM_TO_GROUP[b],
        "cross_group": SYM_TO_GROUP[a] != SYM_TO_GROUP[b],
        "ar1": round(r["ar1"], 4),
        "half_life": round(r["hl"], 0) if r["hl"] < 1e6 else "—",
        "ret_corr": round(return_corr, 3),
        "beta": round(r["beta"], 3),
        "resid_std": round(r["resid_std"], 1),
        "edge_per_sd": round(r["resid_std"] * 0.5, 1),  # rough: half a std dev
    })

results_df = pd.DataFrame(results)
print(f"\nScanned {len(results_df)} pairs total")
print(f"  within-group: {(~results_df['cross_group']).sum()}")
print(f"  cross-group:  {results_df['cross_group'].sum()}")

# ── Filter to "interesting" pairs: short half-life AND meaningful return corr
print("\n=== TOP 20 cross-group pairs by FASTEST half-life ===")
print("(Lower half-life = spread reverts faster = more harvestable)")
cross = results_df[results_df["cross_group"]].copy()
cross_sorted = cross[cross["half_life"] != "—"].sort_values("half_life").head(20)
print(cross_sorted[["a", "b", "ar1", "half_life", "ret_corr", "beta", "resid_std"]]
      .to_string(index=False))

print("\n=== TOP 20 cross-group pairs by HIGHEST return correlation ===")
print("(High return-corr without same-group classification = potential hidden link)")
top_corr = cross.copy()
top_corr["abs_ret_corr"] = top_corr["ret_corr"].abs()
top_corr_sorted = top_corr.sort_values("abs_ret_corr", ascending=False).head(20)
print(top_corr_sorted[["a", "b", "ret_corr", "ar1", "half_life", "resid_std"]]
      .to_string(index=False))

print("\n=== TOP cross-group: BOTH fast half-life AND high return correlation ===")
print("(Gold standard for stat-arb: high corr means tight tracking, "
      "fast HL means spread harvestable)")
gold = cross[(cross["half_life"] != "—")].copy()
gold["abs_corr"] = gold["ret_corr"].abs()
gold = gold[(gold["abs_corr"] >= 0.05) & (gold["half_life"].astype(float) < 200)]
gold_sorted = gold.sort_values(["abs_corr", "half_life"], ascending=[False, True]).head(15)
print(gold_sorted[["a", "b", "a_grp", "b_grp", "ar1", "half_life",
                   "ret_corr", "resid_std"]].to_string(index=False))

# ── Reference: top within-group for sanity
print("\n=== REFERENCE: top 10 within-group by fastest half-life (control) ===")
within = results_df[~results_df["cross_group"]].copy()
within_sorted = within[within["half_life"] != "—"].sort_values("half_life").head(10)
print(within_sorted[["a", "b", "ar1", "half_life", "ret_corr"]].to_string(index=False))

# ── Group-level summary: which group-pair has the most candidates?
print("\n=== Group-pair heatmap: count of cross-group pairs with HL < 200 ===")
fast = cross[(cross["half_life"] != "—")].copy()
fast["hl_num"] = fast["half_life"].astype(float)
fast = fast[fast["hl_num"] < 200]
heatmap = fast.groupby(["a_grp", "b_grp"]).size().unstack(fill_value=0)
print(heatmap)
