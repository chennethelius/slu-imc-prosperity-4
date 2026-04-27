"""Per-option Monte Carlo + portfolio score distribution.

Goal: numerically prove (a) the fair-value of each option, (b) the
upper bound on expected score with the displayed volumes, and (c) the
distribution of realized 100-sim averages under the optimal portfolio.

Each option is simulated independently with its own paths so its
fair value estimate is honest and not anchored to closed-form.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd

# ── Model ─────────────────────────────────────────────────────────────
S0, SIGMA, R = 50.0, 2.51, 0.0
STEPS_PER_DAY = 4
TD_YR = 252
T_2W = 10 / TD_YR
T_3W = 15 / TD_YR
N_PATHS = 200_000
N_SCORE_TRIALS = 5_000   # number of "100-sim averages" to compute

rng = np.random.default_rng(2026_04_27)

def gbm_paths(T, n_paths, steps_per_day=STEPS_PER_DAY, seed=None, with_min=False, with_t1=None):
    """Return (S_T, [min_S], [S_t1]). Uses one np.array per draw."""
    n_steps = int(round(T * TD_YR * steps_per_day))
    dt = T / n_steps
    drift = -0.5 * SIGMA * SIGMA * dt
    vol = SIGMA * math.sqrt(dt)
    local = rng if seed is None else np.random.default_rng(seed)
    Z = local.standard_normal((n_paths, n_steps))
    log_increments = drift + vol * Z
    log_path = np.cumsum(log_increments, axis=1)
    log_path = np.concatenate([np.zeros((n_paths, 1)), log_path], axis=1)
    S = S0 * np.exp(log_path)
    out = {"S_T": S[:, -1]}
    if with_min: out["min_S"] = S.min(axis=1)
    if with_t1 is not None:
        idx = int(round(with_t1 * TD_YR * steps_per_day))
        out["S_t1"] = S[:, idx]
    return out

# ── Per-option payoff functions ──────────────────────────────────────
def payoff_vanilla_put(S_T, K):  return np.maximum(K - S_T, 0)
def payoff_vanilla_call(S_T, K): return np.maximum(S_T - K, 0)
def payoff_binary_put(S_T, K, payout): return np.where(S_T < K, payout, 0.0)
def payoff_knockout_put(S_T, min_S, K, H):
    return np.where(min_S < H, 0.0, np.maximum(K - S_T, 0))
def payoff_chooser(S_T, S_t1, K):
    """Holder picks ITM side at t1. Under r=0, ITM = more valuable."""
    pick_call = S_t1 > K
    return np.where(pick_call, np.maximum(S_T - K, 0), np.maximum(K - S_T, 0))

# ── Instrument table ─────────────────────────────────────────────────
INSTRS = [
    dict(sym="AC_50_P",    kind="vp",  K=50, T=T_3W, bid=12.00, ask=12.05, max_vol=50),
    dict(sym="AC_50_C",    kind="vc",  K=50, T=T_3W, bid=12.00, ask=12.05, max_vol=50),
    dict(sym="AC_35_P",    kind="vp",  K=35, T=T_3W, bid= 4.33, ask= 4.35, max_vol=50),
    dict(sym="AC_40_P",    kind="vp",  K=40, T=T_3W, bid= 6.50, ask= 6.55, max_vol=50),
    dict(sym="AC_45_P",    kind="vp",  K=45, T=T_3W, bid= 9.05, ask= 9.10, max_vol=50),
    dict(sym="AC_60_C",    kind="vc",  K=60, T=T_3W, bid= 8.80, ask= 8.85, max_vol=50),
    dict(sym="AC_50_P_2",  kind="vp",  K=50, T=T_2W, bid= 9.70, ask= 9.75, max_vol=50),
    dict(sym="AC_50_C_2",  kind="vc",  K=50, T=T_2W, bid= 9.70, ask= 9.75, max_vol=50),
    dict(sym="AC_50_CO",   kind="ch",  K=50, T=T_3W, t1=T_2W, bid=22.20, ask=22.30, max_vol=50),
    dict(sym="AC_40_BP",   kind="bp",  K=40, T=T_3W, payout=10.0, bid=5.00, ask=5.10, max_vol=50),
    dict(sym="AC_45_KO",   kind="ko",  K=45, T=T_3W, H=35, bid=0.150, ask=0.175, max_vol=500),
]

# ── Per-option MC ────────────────────────────────────────────────────
print("=" * 92)
print("Per-option Monte Carlo (independent paths per instrument; N =", N_PATHS, "paths)")
print("=" * 92)
rows = []
for inst in INSTRS:
    needs_min = inst["kind"] == "ko"
    needs_t1  = inst.get("t1")
    paths = gbm_paths(inst["T"], N_PATHS, with_min=needs_min, with_t1=needs_t1)
    if   inst["kind"] == "vp":  pay = payoff_vanilla_put(paths["S_T"], inst["K"])
    elif inst["kind"] == "vc":  pay = payoff_vanilla_call(paths["S_T"], inst["K"])
    elif inst["kind"] == "bp":  pay = payoff_binary_put(paths["S_T"], inst["K"], inst["payout"])
    elif inst["kind"] == "ko":  pay = payoff_knockout_put(paths["S_T"], paths["min_S"], inst["K"], inst["H"])
    elif inst["kind"] == "ch":  pay = payoff_chooser(paths["S_T"], paths["S_t1"], inst["K"])

    fair = pay.mean()
    se   = pay.std(ddof=1) / math.sqrt(N_PATHS)
    p5, p50, p95 = np.percentile(pay, [5, 50, 95])
    inst["fair"] = fair
    edge_buy  = fair - inst["ask"]
    edge_sell = inst["bid"] - fair
    if   edge_sell > 0 and edge_sell >= edge_buy: side, edge, px = "SELL", edge_sell, inst["bid"]
    elif edge_buy  > 0:                            side, edge, px = "BUY",  edge_buy,  inst["ask"]
    else:                                          side, edge, px = "HOLD", max(edge_buy, edge_sell), None
    inst["side"], inst["edge"], inst["trade_px"] = side, edge, px
    expected = edge * inst["max_vol"] if side != "HOLD" else 0.0
    rows.append({
        "symbol": inst["sym"], "fair": round(fair, 4), "MC_se": round(se, 4),
        "bid": inst["bid"], "ask": inst["ask"],
        "buy_edge": round(edge_buy, 4), "sell_edge": round(edge_sell, 4),
        "side": side, "max_vol": inst["max_vol"], "E[PnL]": round(expected, 2),
        "pay_p5": round(p5, 2), "pay_p50": round(p50, 2), "pay_p95": round(p95, 2),
    })

df = pd.DataFrame(rows)
pd.set_option("display.width", 220)
pd.set_option("display.max_columns", None)
print(df.to_string(index=False))
total = df["E[PnL]"].sum()
print(f"\nTotal expected score under optimal portfolio: {total:.2f} SeaShells\n")

# ── Portfolio realized-score distribution ────────────────────────────
# Simulate the full 100-sim scoring procedure many times to see how
# noisy the realized score is around its expected +XX.
print("=" * 92)
print("Distribution of realized 100-sim averages under the optimal portfolio")
print("=" * 92)
print(f"({N_SCORE_TRIALS} independent trials, each = mean of 100 GBM paths)")

orders = [
    {**i, "qty": (i["max_vol"] if i["side"] == "BUY" else -i["max_vol"]),
     "px": i["trade_px"]}
    for i in INSTRS if i["side"] != "HOLD"
]

# Joint MC: simulate one path, evaluate all orders, sum to per-sim PnL.
def one_score(n_sims=100):
    """Simulate `n_sims` paths jointly (3w horizon) and return the MEAN PnL."""
    n_steps = int(round(T_3W * TD_YR * STEPS_PER_DAY))
    dt = T_3W / n_steps
    drift = -0.5 * SIGMA * SIGMA * dt
    vol = SIGMA * math.sqrt(dt)
    Z = rng.standard_normal((n_sims, n_steps))
    log_inc = drift + vol * Z
    log_path = np.concatenate([np.zeros((n_sims, 1)), np.cumsum(log_inc, axis=1)], axis=1)
    S = S0 * np.exp(log_path)
    S_T = S[:, -1]
    min_S = S.min(axis=1)
    idx_t1 = int(round(T_2W * TD_YR * STEPS_PER_DAY))
    S_t1 = S[:, idx_t1]
    pnls = np.zeros(n_sims)
    for o in orders:
        if   o["kind"] == "vp": pay = np.maximum(o["K"] - (S_t1 if o["T"] == T_2W else S_T), 0)
        elif o["kind"] == "vc": pay = np.maximum((S_t1 if o["T"] == T_2W else S_T) - o["K"], 0)
        elif o["kind"] == "bp": pay = np.where(S_T < o["K"], o["payout"], 0.0)
        elif o["kind"] == "ko": pay = np.where(min_S < o["H"], 0.0, np.maximum(o["K"] - S_T, 0))
        elif o["kind"] == "ch": pay = np.where(S_t1 > o["K"], np.maximum(S_T - o["K"], 0), np.maximum(o["K"] - S_T, 0))
        pnls += o["qty"] * (pay - o["px"])
    return pnls.mean(), pnls   # per-trial mean + the 100-sim distribution itself

means = np.zeros(N_SCORE_TRIALS)
last_pnls = None
for i in range(N_SCORE_TRIALS):
    means[i], last_pnls = one_score(100)

p1, p5, p25, p50, p75, p95, p99 = np.percentile(means, [1,5,25,50,75,95,99])
print(f"\nRealized 100-sim score:  mean = {means.mean():+.2f}   std = {means.std(ddof=1):.2f}")
print(f"  P1   = {p1:+8.2f}")
print(f"  P5   = {p5:+8.2f}    <-- 90% interval")
print(f"  P25  = {p25:+8.2f}")
print(f"  P50  = {p50:+8.2f}")
print(f"  P75  = {p75:+8.2f}")
print(f"  P95  = {p95:+8.2f}    <-- 90% interval")
print(f"  P99  = {p99:+8.2f}")

# Per-simulation (single path) variance — extreme single-path swings
print("\nPer-SIMULATION PnL distribution (single GBM path, NOT average):")
sp1, sp5, sp50, sp95, sp99 = np.percentile(last_pnls, [1, 5, 50, 95, 99])
print(f"  P1 / P5 / P50 / P95 / P99 = {sp1:+.1f} / {sp5:+.1f} / {sp50:+.1f} / {sp95:+.1f} / {sp99:+.1f}")
print(f"  min = {last_pnls.min():+.1f}   max = {last_pnls.max():+.1f}")

# ── Hard upper bound check ───────────────────────────────────────────
print("\n" + "=" * 92)
print("Upper-bound sanity check (PHYSICALLY possible payoff per simulation)")
print("=" * 92)
max_per_inst = []
for inst in INSTRS:
    v = inst["max_vol"]
    if   inst["kind"] == "vp": pmax = inst["K"]                        # S_T → 0
    elif inst["kind"] == "vc": pmax = 1000                              # uncapped — placeholder
    elif inst["kind"] == "bp": pmax = inst["payout"]
    elif inst["kind"] == "ko": pmax = inst["K"] - inst["H"] + 1e-9      # bounded by S_T > H
    elif inst["kind"] == "ch": pmax = max(inst["K"], 1000)
    max_per_inst.append((inst["sym"], v, pmax, v * pmax))
print(f"{'Symbol':<12} {'Vol':>5} {'MaxPay':>10} {'MaxNotional':>14}")
for s, v, p, n in max_per_inst:
    print(f"{s:<12} {v:>5} {p:>10.1f} {n:>14.1f}")
print(f"\nThese are theoretical ceilings, not realistic outcomes. The realized")
print(f"P1-P99 range above gives the actually-attainable realized-score band.")
