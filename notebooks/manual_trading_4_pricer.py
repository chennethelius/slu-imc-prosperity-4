"""Manual Trading 4 — Aether Crystal options pricer.

Underlying: AETHER_CRYSTAL, S0=50, GBM, r=0, q=0, sigma=2.51 (251%).
Discrete grid: 4 steps per trading day, 252 trading days per year.

Convention from problem text:
    "2 weeks" = 10 trading days  -> T = 10/252 yr
    "3 weeks" = 15 trading days  -> T = 15/252 yr

Outputs fair values, edges vs bid/ask, and the optimal portfolio
(maximizing expected PnL within volume caps).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

# ---------------- model parameters ----------------
S0 = 50.0
SIGMA = 2.51
R = 0.0
STEPS_PER_DAY = 4
TRADING_DAYS_PER_YEAR = 252
T_2W = 10 / 252  # 2-week instruments
T_3W = 15 / 252  # 3-week instruments
T_CHOICE = T_2W  # chooser decision time

# ---------------- BS helpers (r=q=0) ----------------
def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bs_call(S: float, K: float, T: float, sigma: float = SIGMA) -> float:
    if T <= 0:
        return max(S - K, 0.0)
    vt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / vt
    d2 = d1 - vt
    return S * _phi(d1) - K * _phi(d2)

def bs_put(S: float, K: float, T: float, sigma: float = SIGMA) -> float:
    if T <= 0:
        return max(K - S, 0.0)
    vt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / vt
    d2 = d1 - vt
    return K * _phi(-d2) - S * _phi(-d1)

# ---------------- exotic pricers ----------------
def chooser_fair(S: float, K: float, t1: float, T2: float, sigma: float = SIGMA) -> float:
    """Chooser at t1, expiry T2. r=q=0 decomposition:
        chooser = Put(K, T2) + Call(K, t1)
    Derivation: at t1, value = max(C(S1,K,T2-t1), P(S1,K,T2-t1))
                              = P(S1,K,T2-t1) + max(0, S1-K)   (parity, r=0)
    Today's value = E[P(S1,K,T2-t1)] + E[(S1-K)+]
                  = Put(S0,K,T2) + Call(S0,K,t1)              (martingale)
    """
    return bs_put(S, K, T2, sigma) + bs_call(S, K, t1, sigma)

def binary_put_fair(S: float, K: float, payout: float, T: float, sigma: float = SIGMA) -> float:
    """Cash-or-nothing put. Pays `payout` if S(T) < K, else 0. r=0."""
    if T <= 0:
        return payout if S < K else 0.0
    vt = sigma * math.sqrt(T)
    d2 = (math.log(S / K) - 0.5 * sigma * sigma * T) / vt
    return payout * _phi(-d2)

def knockout_put_mc(
    S: float, K: float, H: float, T: float,
    sigma: float = SIGMA, steps_per_day: int = STEPS_PER_DAY,
    n_paths: int = 400_000, seed: int = 42,
) -> tuple[float, float]:
    """Down-and-out put with discrete monitoring at `steps_per_day` per day.
    Knocked out if S ever observed strictly below H. Returns (mean, stderr)."""
    rng = np.random.default_rng(seed)
    n_steps = int(round(T * TRADING_DAYS_PER_YEAR * steps_per_day))
    dt = T / n_steps
    drift = -0.5 * sigma * sigma * dt
    vol = sigma * math.sqrt(dt)

    n = n_paths
    log_s = np.full(n, math.log(S))
    alive = np.ones(n, dtype=bool)
    log_H = math.log(H)

    for _ in range(n_steps):
        log_s += drift + vol * rng.standard_normal(n)
        # knock-out: any observation strictly below H
        alive &= log_s >= log_H

    S_T = np.exp(log_s)
    payoff = np.where(alive, np.maximum(K - S_T, 0.0), 0.0)
    return float(payoff.mean()), float(payoff.std(ddof=1) / math.sqrt(n))

# ---------------- instrument table ----------------
@dataclass
class Inst:
    sym: str
    kind: str          # vanilla_call/put, chooser, binary_put, knockout_put, underlying
    strike: float
    bid: float
    ask: float
    max_vol: int
    T: float
    barrier: float | None = None
    payout: float | None = None
    t_choice: float | None = None
    fair: float | None = None

INSTRS = [
    # 3-week vanillas (T=15/252)
    Inst("AC_50_P",  "vanilla_put",  50, 12.00, 12.05, 50, T_3W),
    Inst("AC_50_C",  "vanilla_call", 50, 12.00, 12.05, 50, T_3W),
    Inst("AC_35_P",  "vanilla_put",  35,  4.33,  4.35, 50, T_3W),
    Inst("AC_40_P",  "vanilla_put",  40,  6.50,  6.55, 50, T_3W),
    Inst("AC_45_P",  "vanilla_put",  45,  9.05,  9.10, 50, T_3W),
    Inst("AC_60_C",  "vanilla_call", 60,  8.80,  8.85, 50, T_3W),
    # 2-week vanillas (T=10/252)
    Inst("AC_50_P_2","vanilla_put",  50,  9.70,  9.75, 50, T_2W),
    Inst("AC_50_C_2","vanilla_call", 50,  9.70,  9.75, 50, T_2W),
    # exotics
    Inst("AC_50_CO", "chooser",      50, 22.20, 22.30, 50, T_3W, t_choice=T_2W),
    Inst("AC_40_BP", "binary_put",   40,  5.00,  5.10, 50, T_3W, payout=10.0),
    Inst("AC_45_KO", "knockout_put", 45,  0.15,  0.175, 500, T_3W, barrier=35.0),
]

def fair_value(inst: Inst) -> float:
    if inst.kind == "vanilla_call":
        return bs_call(S0, inst.strike, inst.T)
    if inst.kind == "vanilla_put":
        return bs_put(S0, inst.strike, inst.T)
    if inst.kind == "chooser":
        return chooser_fair(S0, inst.strike, inst.t_choice, inst.T)
    if inst.kind == "binary_put":
        return binary_put_fair(S0, inst.strike, inst.payout, inst.T)
    if inst.kind == "knockout_put":
        mean, _ = knockout_put_mc(S0, inst.strike, inst.barrier, inst.T)
        return mean
    raise ValueError(inst.kind)

# ---------------- compute and report ----------------
rows = []
for inst in INSTRS:
    fair = fair_value(inst)
    inst.fair = fair
    edge_buy  = fair - inst.ask           # positive = good to buy
    edge_sell = inst.bid - fair           # positive = good to sell
    if edge_sell >= edge_buy and edge_sell > 0:
        side, edge = "SELL", edge_sell
    elif edge_buy > 0:
        side, edge = "BUY", edge_buy
    else:
        side, edge = "HOLD", max(edge_buy, edge_sell)
    expected = edge * inst.max_vol if side != "HOLD" else 0.0
    rows.append({
        "symbol": inst.sym,
        "kind": inst.kind,
        "K": inst.strike,
        "T_days": int(round(inst.T * 252)),
        "bid": inst.bid,
        "ask": inst.ask,
        "fair": round(fair, 4),
        "edge_buy(ask)":  round(edge_buy,  4),
        "edge_sell(bid)": round(edge_sell, 4),
        "side": side,
        "size": inst.max_vol if side != "HOLD" else 0,
        "E[PnL]": round(expected, 2),
    })

df = pd.DataFrame(rows)
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", None)
print("=== Fair values & edges (sigma=2.51, r=0, S0=50) ===")
print(df.to_string(index=False))

print()
print("=== Recommended portfolio (max E[PnL] within volume caps) ===")
chosen = df[df["side"] != "HOLD"].copy()
print(chosen[["symbol", "side", "size", "bid", "ask", "fair", "E[PnL]"]].to_string(index=False))
total = chosen["E[PnL]"].sum()
print(f"\nTotal expected PnL across all trades: {total:.2f} SeaShells")
print("(Avg over 100 GBM sims; per-sim variance is non-zero and not reported here.)")
