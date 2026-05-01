"""Round 3 — Test_1 verbatim, with the VEV anchor replaced by BS-with-smile.

Why this is the minimal-change strategy
---------------------------------------
Test_1.py earns +180,851 on round-3 day 0 via the historical backtester. Its
VEV pipeline fades divergences from a running-mean *anchor* (warmup 100 ticks).
The running mean is a noisy estimator of the option's true fair value, and it
ignores the underlying entirely.

v01 keeps every line of Test_1's logic and changes one thing: the anchor for
the VEV divergence trader is no longer the running mean of the option's mid,
it is **the Black-Scholes call price** computed from
    S = VELVETFRUIT_EXTRACT microprice
    K = strike (4000..5500)
    T = ticks-to-expiry (round-end)
    sigma_K = per-strike implied vol from the calibration (the volatility
              smile — fitted once via round3_mc/fit_round3.py).

This grounds the anchor in arbitrage-free theory and uses the **smile**
constants directly. Every other parameter (TAKE_WIDTH, ANCHOR_WARMUP,
DIVERGE_TAKE_SIZE, the per-strike `diverge_threshold`, both Kalman-MR
configs) is identical to Test_1.

Informed-flow tilt: an exponentially-decayed signed market-trade volume per
option (informed-flow proxy — IMC P4 trade logs are anonymised, no named
counterparties exist). When |flow| exceeds 1 lot the divergence threshold
is widened on the opposing side (don't fight informed flow) and tightened
on the agreeing side (act sooner with the flow). One added parameter:
FLOW_DECAY.
"""

import json
import math
from typing import Any

from datamodel import (
    Listing,
    Observation,
    Order,
    OrderDepth,
    ProsperityEncoder,
    Symbol,
    Trade,
    TradingState,
)


# =========================================================================
# Logger — IMC P4 Visualizer compatible (rkothari3/IMC_P4_Visualizer)
# =========================================================================


class Logger:
    def __init__(self) -> None:
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects: Any, sep: str = " ", end: str = "\n") -> None:
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state, orders, conversions, trader_data):
        base = len(self.to_json([self.compress_state(state, ""),
                                 self.compress_orders(orders), conversions, "", ""]))
        m = (self.max_log_length - base) // 3
        print(self.to_json([self.compress_state(state, self.truncate(state.traderData, m)),
                            self.compress_orders(orders), conversions,
                            self.truncate(trader_data, m), self.truncate(self.logs, m)]))
        self.logs = ""

    def compress_state(self, s, td):
        return [s.timestamp, td, self.compress_listings(s.listings),
                self.compress_order_depths(s.order_depths),
                self.compress_trades(s.own_trades),
                self.compress_trades(s.market_trades),
                s.position, self.compress_observations(s.observations)]

    def compress_listings(self, ls):
        return [[l.symbol, l.product, l.denomination] for l in ls.values()]

    def compress_order_depths(self, ods):
        return {s: [od.buy_orders, od.sell_orders] for s, od in ods.items()}

    def compress_trades(self, trades):
        return [[t.symbol, t.price, t.quantity, t.buyer, t.seller, t.timestamp]
                for arr in trades.values() for t in arr]

    def compress_observations(self, obs):
        co = {p: [o.bidPrice, o.askPrice, o.transportFees, o.exportTariff, o.importTariff]
              for p, o in obs.conversionObservations.items()}
        return [obs.plainValueObservations, co]

    def compress_orders(self, orders):
        return [[o.symbol, o.price, o.quantity] for arr in orders.values() for o in arr]

    def to_json(self, v):
        return json.dumps(v, cls=ProsperityEncoder, separators=(",", ":"))

    def truncate(self, value, max_length):
        lo, hi = 0, min(len(value), max_length)
        out = ""
        while lo <= hi:
            mid = (lo + hi) // 2
            cand = value[:mid] + ("..." if mid < len(value) else "")
            if len(json.dumps(cand)) <= max_length:
                out = cand
                lo = mid + 1
            else:
                hi = mid - 1
        return out


logger = Logger()


# =========================================================================
# Black-Scholes (pure Python — scipy not available in submission)
# =========================================================================


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S: float, K: float, T: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return max(S - K, 0.0)
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    return S * _norm_cdf(d1) - K * _norm_cdf(d2)


# =========================================================================
# Calibration constants (volatility smile + structural constants)
# =========================================================================

T_EXPIRY = 30_000   # ticks at round-3 day-0 start; expiry at round end
TICK_STEP = 100

# Median per-strike implied vol from 3-day fit (round3_mc/calibration.json).
# Used as the BS anchor sigma per option.
SIGMA_SMILE = {
    4000: 0.0008960,
    4500: 0.0004921,
    5000: 0.0002616,
    5100: 0.0002558,
    5200: 0.0002671,
    5300: 0.0002705,
    5400: 0.0002515,
    5500: 0.0002697,
}

# Test_1 constants (frozen — proven 180k on day 0 via imc-p4-bt)
TAKE_WIDTH = 1
ANCHOR_WARMUP = 100
DIVERGE_TAKE_SIZE = 30

# Single new parameter — flow EMA half-life ≈ 1 / (1 - decay) ticks
FLOW_DECAY = 0.92


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


def microprice(depth):
    bb = max(depth.buy_orders); ba = min(depth.sell_orders)
    bv = depth.buy_orders[bb]; av = -depth.sell_orders[ba]
    tot = bv + av
    return (bb * av + ba * bv) / tot if tot > 0 else (bb + ba) / 2.0


# =========================================================================
# VEV pipeline — Test_1 divergence/take/MM, with BS-with-smile anchor
# =========================================================================


def update_flow(scratch, market_trades, bb, ba):
    """Decayed signed volume from anonymised market_trades (informed-flow proxy)."""
    flow = scratch.get("_flow", 0.0) * FLOW_DECAY
    for t in market_trades or []:
        if t.price >= ba:
            flow += t.quantity
        elif t.price <= bb:
            flow -= t.quantity
    scratch["_flow"] = flow
    return flow


def divergence_take_orders(cfg, depth, scratch, position, anchor, mid):
    threshold = cfg.get("diverge_threshold", 0)
    flow = scratch.get("_flow", 0.0)
    # Flow tilt: with-flow → tighter threshold; against-flow → wider threshold.
    diverge = mid - anchor
    if diverge > 0 and flow > 1.0:
        threshold = max(1, threshold - 1)   # informed buyers + overpriced → trim
    elif diverge < 0 and flow < -1.0:
        threshold = max(1, threshold - 1)   # informed sellers + underpriced → buy
    elif diverge > 0 and flow < -1.0:
        threshold = threshold + 2           # against-flow fade → be more cautious
    elif diverge < 0 and flow > 1.0:
        threshold = threshold + 2

    if threshold <= 0 or scratch.get("anchor_n", 0) < ANCHOR_WARMUP:
        return [], 0, 0
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


def vev_orders(cfg, state, scratch, vev_S, ttx):
    """VEV order generator — divergence (BS-anchored) + take + MM."""
    depth = state.order_depths.get(cfg["product"])
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []

    best_bid = max(depth.buy_orders)
    best_ask = min(depth.sell_orders)
    mid = (best_bid + best_ask) / 2
    fair = full_depth_mid(depth)

    # BS anchor with smile — replaces the running-mean anchor in Test_1.
    K = cfg["strike"]
    sigma_K = SIGMA_SMILE.get(K, 0.000265)  # fallback ≈ smile baseline
    anchor = bs_call(vev_S, K, ttx, sigma_K)

    # Warmup counter (still gated to give the flow EMA time to build).
    n = scratch.get("anchor_n", 0) + 1
    scratch["anchor_n"] = n

    # Update flow EMA.
    update_flow(scratch, state.market_trades.get(cfg["product"], []), best_bid, best_ask)

    position = state.position.get(cfg["product"], 0)
    diverge, d_bought, d_sold = divergence_take_orders(
        cfg, depth, scratch, position, anchor, mid
    )
    pos_eff = position + d_bought - d_sold
    takes, bought, sold = take_orders(cfg, depth, fair, pos_eff)
    bought += d_bought; sold += d_sold
    quotes = make_quote(cfg, fair, best_bid, best_ask, position, bought, sold)
    return diverge + takes + quotes


# =========================================================================
# Underlier pipeline (Kalman-MR, frozen verbatim from Test_1)
# =========================================================================


KALMAN_MR_PRODUCTS = [
    # Conservative position_limit=100 — slu CI's rust backtester defaults
    # unknown products to 100 and rejects whole-tick orders that would breach.
    # Higher caps (200/300 in Test_1) trigger silent wholesale rejections.
    {"product": "HYDROGEL_PACK", "position_limit": 100, "k_ss": 0.02,
     "fair_static": 10030, "mr_gain": 2000, "sigma_init": 30.0,
     "take_max_pay": -6, "quote_edge": 3, "quote_size": 30},
    {"product": "VELVETFRUIT_EXTRACT", "position_limit": 100, "k_ss": 0.02,
     "fair_static": 5275, "mr_gain": 2000, "sigma_init": 15.0,
     "take_max_pay": -2, "quote_edge": 1, "quote_size": 30},
]


def kalman_mr_orders(cfg, depth, position, scratch):
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    product, limit = cfg["product"], cfg["position_limit"]
    bb = max(depth.buy_orders); ba = min(depth.sell_orders)
    bv_tob = depth.buy_orders[bb]; av_tob = -depth.sell_orders[ba]
    tot = bv_tob + av_tob
    micro = (bb * av_tob + ba * bv_tob) / tot if tot > 0 else (bb + ba) / 2.0
    mid = (bb + ba) / 2.0

    k_ss = cfg["k_ss"]
    fair = scratch.get("_f", micro)
    innov = micro - fair
    err_ema = scratch.get("_err", abs(innov))
    err_ema += k_ss * (abs(innov) - err_ema)
    fair += (k_ss / (1.0 + err_ema)) * innov
    scratch["_f"], scratch["_err"] = fair, err_ema

    n = scratch.get("_n", 0) + 1
    s2 = scratch.get("_s2", 0.0) + (mid - fair) ** 2
    scratch["_n"], scratch["_s2"] = n, s2
    sigma = max(1.0, (s2 / n) ** 0.5) if n > 50 else cfg["sigma_init"]

    anchor = cfg["fair_static"]
    target = max(-limit, min(limit, round(cfg["mr_gain"] * (anchor - mid) / sigma)))

    take_max_pay = cfg["take_max_pay"]
    quote_edge = cfg["quote_edge"]
    quote_size = cfg["quote_size"]

    orders, bv, sv = [], 0, 0
    delta = target - position
    if delta > 0:
        for a in sorted(depth.sell_orders):
            if a > fair + take_max_pay: break
            room = min(-depth.sell_orders[a], delta - bv, limit - position - bv)
            if room <= 0: break
            orders.append(Order(product, a, room)); bv += room
    elif delta < 0:
        need = -delta
        for b in sorted(depth.buy_orders, reverse=True):
            if b < fair - take_max_pay: break
            room = min(depth.buy_orders[b], need - sv, limit + position - sv)
            if room <= 0: break
            orders.append(Order(product, b, -room)); sv += room

    baaf = min((p for p in depth.sell_orders if p >= fair + quote_edge), default=None)
    bbbf = max((p for p in depth.buy_orders if p <= fair - quote_edge), default=None)
    if bbbf is not None:
        buy_q = min(quote_size, limit - position - bv)
        if buy_q > 0: orders.append(Order(product, bbbf + 1, buy_q))
    if baaf is not None:
        sell_q = min(quote_size, limit + position - sv)
        if sell_q > 0: orders.append(Order(product, baaf - 1, -sell_q))
    return orders


# =========================================================================
# VEV per-product config (thresholds frozen from Test_1)
# =========================================================================


VEV_PRODUCTS = [
    {"product": "VEV_4000", "strike": 4000, "position_limit": 100, "quote_size": 30, "diverge_threshold": 25, "max_diverge_position": 95},
    {"product": "VEV_4500", "strike": 4500, "position_limit": 100, "quote_size": 30, "diverge_threshold": 25, "max_diverge_position": 95},
    {"product": "VEV_5000", "strike": 5000, "position_limit": 100, "quote_size": 30, "diverge_threshold": 22, "max_diverge_position": 95},
    {"product": "VEV_5100", "strike": 5100, "position_limit": 100, "quote_size": 30, "diverge_threshold": 18, "max_diverge_position": 95},
    {"product": "VEV_5200", "strike": 5200, "position_limit": 100, "quote_size": 30, "diverge_threshold": 14, "max_diverge_position": 95},
    {"product": "VEV_5300", "strike": 5300, "position_limit": 100, "quote_size": 30, "diverge_threshold": 10, "max_diverge_position": 95},
    {"product": "VEV_5400", "strike": 5400, "position_limit": 100, "quote_size": 30, "diverge_threshold": 5,  "max_diverge_position": 95},
    {"product": "VEV_5500", "strike": 5500, "position_limit": 100, "quote_size": 30, "diverge_threshold": 3,  "max_diverge_position": 95},
]


# =========================================================================
# Trader
# =========================================================================


class Trader:
    def bid(self) -> int:
        return 0

    def run(self, state: TradingState):
        try:
            store = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            store = {}

        orders: dict[str, list[Order]] = {}

        # Underliers
        for cfg in KALMAN_MR_PRODUCTS:
            depth = state.order_depths.get(cfg["product"])
            ors = kalman_mr_orders(
                cfg, depth, state.position.get(cfg["product"], 0),
                store.setdefault(cfg["product"], {}),
            )
            if ors:
                orders[cfg["product"]] = ors

        # VEV options — need VFE microprice as the BS underlying input
        vfe_depth = state.order_depths.get("VELVETFRUIT_EXTRACT")
        if vfe_depth and vfe_depth.buy_orders and vfe_depth.sell_orders:
            vev_S = microprice(vfe_depth)
            ttx = max(1.0, T_EXPIRY - state.timestamp / TICK_STEP)
            for cfg in VEV_PRODUCTS:
                ors = vev_orders(
                    cfg, state, store.setdefault(cfg["product"], {}), vev_S, ttx,
                )
                if ors:
                    orders[cfg["product"]] = ors

        trader_data = json.dumps(store)
        logger.flush(state, orders, 0, trader_data)
        return orders, 0, trader_data
