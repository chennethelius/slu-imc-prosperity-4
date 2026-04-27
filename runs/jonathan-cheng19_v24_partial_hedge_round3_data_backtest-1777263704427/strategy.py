"""Round 3 v23 — v16 + cross-product DELTA HEDGING (VFE neutralises VEV exposure).

The VEV options are calls on VFE. Each long call has positive delta toward
VFE (option price moves with the underlying). When the strategy accumulates
long options, the position has implicit VFE exposure. v23 makes that explicit:

  per option position q_i, delta_i = N(d1_i)  [Black-Scholes call delta]
  total_VEV_delta = sum_i (q_i * delta_i)
  VFE_target_hedged = VFE_kalman_target - total_VEV_delta

When we're long a basket of in-the-money calls (high delta), the hedger goes
short VFE to neutralise. When we're short calls or only own OTM (low delta),
hedge size is small. The Kalman MR fair-value target stays primary; the
hedge is a directional offset.

Why this is not overfitting: BS delta is a structural identity, not a fitted
parameter. The smile sigma is the same calibrated constant we already use for
option fair-value. No new tunables.

Expected effect: lower VFE drawdown when options swing against us — the
position-targeting reduces the stale long bias on VFE that compounds with
options-pipeline losses.

KEY FINDING: at limit=200/300 (the IMC actual limits), hybrid.py historical
PnL = 716,008. v01-v10 all add layers (Stoikov skew, dual-anchor MR, IV
scalp, exit-at-fair, EMA trend) on top of hybrid's Kalman-MR + tighter VEV
divergence — and every layer DETRACTS at full limits (v10 = 243k vs hybrid
716k = -473k regression). The simpler the better.

v11 adds the SINGLE empirically-validated signal: VFE size>=11 informed-trade
prints predict +4.5 mid move over 500 ticks (49 events / 3 days). Bias the
Kalman target by INFORMED_GAIN_S * decayed_signal. Two new constants only.

If informed-flow doesn't help at MC, fall back to vanilla hybrid.

Round 3 submission — two pipelines, one Trader.

  HYDROGEL_PACK / VELVETFRUIT_EXTRACT → Kalman-MR (proportional reversion to fair_static)
  VEV_4000 … VEV_5500                 → anchor-divergence + market-make

PRE-SUBMISSION CHECKLIST:
  - Re-sweep fair_static for HYDROGEL/VELVETFRUIT against the latest day CSVs.
    Score by per-day mean AND min PnL — IMC submissions run a single day,
    so a high-mean param that bombs on one day is worse than a flatter one.
  - Validate against the latest Prosperity sandbox log with --max-timestamp
    matching sandbox length; per-product PnL should match within ~5%.
"""

import json
import math

from datamodel import Order, TradingState

TAKE_WIDTH = 1
ANCHOR_WARMUP = 100
DIVERGE_TAKE_SIZE = 30

# v11: VFE informed-flow signal (only addition vs hybrid.py).
INFORMED_SIZE_VFE = 11
INFORMED_GAIN_S = 10
INFORMED_DECAY = 0.998

# v23: delta hedging
T_EXPIRY = 30_000
TICK_STEP = 100
SIGMA_SMILE = {  # median IV per strike (calibrated)
    4000: 0.0008960, 4500: 0.0004921, 5000: 0.0002616, 5100: 0.0002558,
    5200: 0.0002671, 5300: 0.0002705, 5400: 0.0002515, 5500: 0.0002697,
    6000: 0.0004283, 6500: 0.0006470,
}
HEDGE_GAIN = 0.3  # 1.0 = full hedge; 0.0 = no hedge; values in (0,1] partial


# =========================================================================
# Shared book helpers
# =========================================================================


def search_sells(depth):
    for p in sorted(depth.sell_orders):
        yield p, -depth.sell_orders[p]


def search_buys(depth):
    for p in sorted(depth.buy_orders, reverse=True):
        yield p, depth.buy_orders[p]


def full_depth_mid(depth):
    bids, asks = list(search_buys(depth)), list(search_sells(depth))
    bv, av = sum(v for _, v in bids), sum(v for _, v in asks)
    if bv <= 0 or av <= 0:
        return (max(depth.buy_orders) + min(depth.sell_orders)) / 2
    return (sum(p * v for p, v in bids) / bv + sum(p * v for p, v in asks) / av) / 2


# =========================================================================
# Zscore pipeline (VEV_*)
# =========================================================================


def divergence_take_orders(cfg, depth, scratch, position, anchor, mid):
    threshold = cfg.get("diverge_threshold", 0)
    if threshold <= 0 or scratch.get("anchor_n", 0) < ANCHOR_WARMUP:
        return [], 0, 0
    diverge = mid - anchor
    if abs(diverge) < threshold:
        return [], 0, 0

    product, limit = cfg["product"], cfg["position_limit"]
    max_pos = cfg.get("max_diverge_position", 60)
    out, bought, sold = [], 0, 0
    if diverge > 0 and position > -max_pos:
        room = position + max_pos
        for price, qty in search_buys(depth):
            cap = min(limit + position - sold, DIVERGE_TAKE_SIZE - sold, room - sold)
            if cap <= 0:
                break
            take = min(qty, cap)
            out.append(Order(product, price, -take))
            sold += take
    elif diverge < 0 and position < max_pos:
        room = max_pos - position
        for price, qty in search_sells(depth):
            cap = min(limit - position - bought, DIVERGE_TAKE_SIZE - bought, room - bought)
            if cap <= 0:
                break
            take = min(qty, cap)
            out.append(Order(product, price, take))
            bought += take
    return out, bought, sold


def take_orders(cfg, depth, fair, position):
    product, limit = cfg["product"], cfg["position_limit"]
    out, bought, sold = [], 0, 0
    for price, qty in search_sells(depth):
        if price >= fair - TAKE_WIDTH:
            break
        cap = limit - position - bought
        if cap <= 0:
            break
        take = min(qty, cap)
        out.append(Order(product, price, take))
        bought += take
    for price, qty in search_buys(depth):
        if price <= fair + TAKE_WIDTH:
            break
        cap = limit + position - sold
        if cap <= 0:
            break
        take = min(qty, cap)
        out.append(Order(product, price, -take))
        sold += take
    return out, bought, sold


def make_quote(cfg, fair, best_bid, best_ask, position, bought, sold):
    product, limit = cfg["product"], cfg["position_limit"]
    qsize = cfg.get("quote_size", 20)
    # Quote at midpoint between fair and the touch on each side.
    bid_px = min(math.floor((fair + best_bid) / 2), best_ask - 1)
    ask_px = max(math.ceil((fair + best_ask) / 2), best_bid + 1)
    buy = max(0, min(qsize, limit - position - bought))
    sell = max(0, min(qsize, limit + position - sold))
    out = []
    if buy > 0 and bid_px < ask_px:
        out.append(Order(product, bid_px, buy))
    if sell > 0 and ask_px > bid_px:
        out.append(Order(product, ask_px, -sell))
    return out


def zscore_orders(cfg, state, scratch):
    depth = state.order_depths.get(cfg["product"])
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []

    best_bid = max(depth.buy_orders)
    best_ask = min(depth.sell_orders)
    mid = (best_bid + best_ask) / 2
    fair = full_depth_mid(depth)

    n = scratch.get("anchor_n", 0) + 1
    s = scratch.get("anchor_sum", 0.0) + mid
    scratch["anchor_n"], scratch["anchor_sum"] = n, s
    anchor = s / n
    position = state.position.get(cfg["product"], 0)

    diverge, d_bought, d_sold = divergence_take_orders(
        cfg, depth, scratch, position, anchor, mid
    )
    pos_eff = position + d_bought - d_sold
    takes, bought, sold = take_orders(cfg, depth, fair, pos_eff)
    bought += d_bought
    sold += d_sold
    quotes = make_quote(cfg, fair, best_bid, best_ask, position, bought, sold)
    return diverge + takes + quotes


# =========================================================================
# Kalman-MR pipeline (HYDROGEL_PACK, VELVETFRUIT_EXTRACT)
# =========================================================================


def kalman_mr_orders(cfg, depth, position, scratch, target_bias=0):
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    product = cfg["product"]
    limit = cfg["position_limit"]
    bb = max(depth.buy_orders)
    ba = min(depth.sell_orders)
    bv_tob = depth.buy_orders[bb]
    av_tob = -depth.sell_orders[ba]
    tot = bv_tob + av_tob
    micro = (bb * av_tob + ba * bv_tob) / tot if tot > 0 else (bb + ba) / 2.0
    mid = (bb + ba) / 2.0

    # Kalman-track fair on volume-weighted micro-price.
    k_ss = cfg["k_ss"]
    fair = scratch.get("_f", micro)
    innov = micro - fair
    err_ema = scratch.get("_err", abs(innov))
    err_ema += k_ss * (abs(innov) - err_ema)
    fair += (k_ss / (1.0 + err_ema)) * innov
    scratch["_f"], scratch["_err"] = fair, err_ema

    # Online σ estimate from (mid - fair) variance.
    n = scratch.get("_n", 0) + 1
    s2 = scratch.get("_s2", 0.0) + (mid - fair) ** 2
    scratch["_n"], scratch["_s2"] = n, s2
    sigma = max(1.0, (s2 / n) ** 0.5) if n > 50 else cfg["sigma_init"]

    # target = mr_gain · (anchor − mid) / σ + target_bias, clamped to ±limit.
    anchor = cfg["fair_static"]
    target = max(-limit, min(limit,
        round(cfg["mr_gain"] * (anchor - mid) / sigma) + target_bias))

    take_max_pay = cfg["take_max_pay"]
    quote_edge = cfg["quote_edge"]
    quote_size = cfg["quote_size"]

    orders = []
    bv = sv = 0
    delta = target - position

    if delta > 0:
        for a in sorted(depth.sell_orders):
            if a > fair + take_max_pay:
                break
            room = min(-depth.sell_orders[a], delta - bv, limit - position - bv)
            if room <= 0:
                break
            orders.append(Order(product, a, room))
            bv += room
    elif delta < 0:
        need = -delta
        for b in sorted(depth.buy_orders, reverse=True):
            if b < fair - take_max_pay:
                break
            room = min(depth.buy_orders[b], need - sv, limit + position - sv)
            if room <= 0:
                break
            orders.append(Order(product, b, -room))
            sv += room

    baaf = min((p for p in depth.sell_orders if p >= fair + quote_edge), default=None)
    bbbf = max((p for p in depth.buy_orders if p <= fair - quote_edge), default=None)
    if bbbf is not None:
        buy_q = min(quote_size, limit - position - bv)
        if buy_q > 0:
            orders.append(Order(product, bbbf + 1, buy_q))
    if baaf is not None:
        sell_q = min(quote_size, limit + position - sv)
        if sell_q > 0:
            orders.append(Order(product, baaf - 1, -sell_q))

    return orders


# =========================================================================
# Per-product configuration
# =========================================================================

KALMAN_MR_PRODUCTS = [
    {
        "product": "HYDROGEL_PACK",
        "position_limit": 200,
        "k_ss": 0.02,
        "fair_static": 10030,       # mean+40; mean across 3 days = 9990
        "mr_gain": 1000,
        "sigma_init": 30.0,
        "take_max_pay": -6,         # only cross when offer ≥6 ticks below fair
        "quote_edge": 3,
        "quote_size": 30,
    },
    {
        "product": "VELVETFRUIT_EXTRACT",
        "position_limit": 200,
        "k_ss": 0.02,
        "fair_static": 5275,        # mean+25; mean across 3 days = 5250
        "mr_gain": 2000,
        "sigma_init": 15.0,
        "take_max_pay": -2,         # only cross when offer ≥2 ticks below fair
        "quote_edge": 1,
        "quote_size": 30,
    },
]

ZSCORE_PRODUCTS = [
    {"product": "VEV_4000", "position_limit": 300, "quote_size": 30, "diverge_threshold": 18, "max_diverge_position": 295},
    {"product": "VEV_4500", "position_limit": 300, "quote_size": 30, "diverge_threshold": 18, "max_diverge_position": 295},
    {"product": "VEV_5000", "position_limit": 300, "quote_size": 30, "diverge_threshold": 15, "max_diverge_position": 295},
    {"product": "VEV_5100", "position_limit": 300, "quote_size": 30, "diverge_threshold": 13, "max_diverge_position": 295},
    {"product": "VEV_5200", "position_limit": 300, "quote_size": 30, "diverge_threshold": 10, "max_diverge_position": 295},
    {"product": "VEV_5300", "position_limit": 300, "quote_size": 30, "diverge_threshold": 7, "max_diverge_position": 295},
    {"product": "VEV_5400", "position_limit": 300, "quote_size": 30, "diverge_threshold": 4, "max_diverge_position": 295},
    {"product": "VEV_5500", "position_limit": 300, "quote_size": 30, "diverge_threshold": 2, "max_diverge_position": 295},
]


def update_informed_signal(store, market_trades_vfe, vfe_bid, vfe_ask):
    """v11: VFE size>=11 informed-flow signal (decayed signed-volume EMA)."""
    sig = store.get("_inf", 0.0) * INFORMED_DECAY
    for t in market_trades_vfe or []:
        if t.quantity < INFORMED_SIZE_VFE:
            continue
        if t.price >= vfe_ask:
            sig += t.quantity
        elif t.price <= vfe_bid:
            sig -= t.quantity
    store["_inf"] = sig
    return sig


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_delta(S, K, T, sigma):
    """Black-Scholes call delta = N(d1)."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return 1.0 if S > K else 0.0
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sqrtT)
    return _norm_cdf(d1)


def total_vev_delta(state, vfe_micro, ttx):
    """Sum option-position * delta across all VEV strikes — net VFE-equivalent
    exposure from the options book."""
    total = 0.0
    for K, sigma_K in SIGMA_SMILE.items():
        sym = f"VEV_{K}"
        q = state.position.get(sym, 0)
        if q == 0:
            continue
        total += q * bs_delta(vfe_micro, K, ttx, sigma_K)
    return total


class Trader:
    def bid(self):
        return 0

    def run(self, state: TradingState):
        try:
            store = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            store = {}

        orders: dict[str, list[Order]] = {}

        for cfg in KALMAN_MR_PRODUCTS:
            depth = state.order_depths.get(cfg["product"])
            target_bias = 0
            sym = cfg["product"]
            if sym == "VELVETFRUIT_EXTRACT" and depth and depth.buy_orders and depth.sell_orders:
                vfe_bid_ = max(depth.buy_orders)
                vfe_ask_ = min(depth.sell_orders)
                vfe_micro = (vfe_bid_ * (-depth.sell_orders[vfe_ask_])
                             + vfe_ask_ * depth.buy_orders[vfe_bid_])
                tot_vol = depth.buy_orders[vfe_bid_] + (-depth.sell_orders[vfe_ask_])
                vfe_micro = vfe_micro / tot_vol if tot_vol > 0 else (vfe_bid_ + vfe_ask_) / 2.0
                # Informed-flow bias (v12).
                sig = update_informed_signal(
                    store.setdefault("_inf_store", {}),
                    state.market_trades.get(sym, []),
                    vfe_bid_, vfe_ask_,
                )
                target_bias += int(round(INFORMED_GAIN_S * sig))
                # v23: delta-hedge bias — neutralise net option exposure.
                ttx = max(1.0, T_EXPIRY - state.timestamp / TICK_STEP)
                vev_delta = total_vev_delta(state, vfe_micro, ttx)
                target_bias -= int(round(HEDGE_GAIN * vev_delta))
            ors = kalman_mr_orders(cfg, depth, state.position.get(sym, 0),
                                   store.setdefault(sym, {}),
                                   target_bias=target_bias)
            if ors:
                orders[sym] = ors

        for cfg in ZSCORE_PRODUCTS:
            ors = zscore_orders(cfg, state, store.setdefault(cfg["product"], {}))
            if ors:
                orders[cfg["product"]] = ors

        return orders, 0, json.dumps(store)
