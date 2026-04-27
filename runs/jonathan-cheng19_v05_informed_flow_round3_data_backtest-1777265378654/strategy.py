"""Round 3 v05 — v04 + informed-trader directional position on VFE.

Bot-behaviour signal mined from data
------------------------------------
Round-3 trade CSVs (3 days) — every product, the trade-size distribution is
small (max 5-6 lots) EXCEPT for VELVETFRUIT_EXTRACT, which has 28 trades of
size >= 13 and 49 trades of size >= 11. These outlier-sized prints are the
informed-trader signature (the "size-15 trader" Chris flagged for P3 round 1).

Predictive test (sign-adjusted future mid move after the print):
    threshold size >= 11:  +1.0 (100t)  +4.5 (500t)  +3.6 (1000t)  +8.0 (2000t)
    threshold size >= 13:  +2.2 (100t)  +2.6 (500t)  +1.8 (1000t)
The size>=11 trades are reliable directional alpha lasting ~500-2000 ticks.

Strategy (one new mechanism on top of v04):
  Per tick, scan state.market_trades['VELVETFRUIT_EXTRACT'] for prints with
  quantity >= INFORMED_SIZE_VFE. For each, classify direction (price >= ask
  is a buy, price <= bid is a sell) and add sign*qty to a decayed informed
  signal stored in traderData. Apply target_position = clip(signal * GAIN,
  -limit, +limit) on VFE; the v04 MM engine then naturally drives toward
  that target via the take/penny-jump path. Options ride the resulting VFE
  move through delta (BS-with-smile fair re-prices each tick).

Parameters added vs v04:
    INFORMED_SIZE_VFE = 11  (size threshold for "big informed trade")
    INFORMED_DECAY    = 0.998 (per-tick decay; ~500-tick half-life)
    INFORMED_GAIN     = 6   (lots of target position per signal unit)
"""

# === v04 docstring follows for reference ===
# Design choices (all from chrispyroberts' P3 winner walkthrough)
# ---------------------------------------------------------------
"""Round 3 v04 — clean P3-finalist market maker (start-from-scratch).

Design choices (all from chrispyroberts' P3 winner walkthrough)
---------------------------------------------------------------
HYDROGEL_PACK   stable-fair MM at fair = 10,000 (the IMC-published true value)
                with the three classic optimizations:
                    1. Fair value taking — cross when ask < fair OR bid > fair
                    2. Position reducing AT fair — take orders priced *at*
                       int(fair) when they reduce |position|, even though
                       they don't move PnL (frees inventory for higher-margin
                       fills next tick — the "third optimization" most teams
                       missed in P3 round 1)
                    3. Penny jumping — post bid at best_bid+1, ask at
                       best_ask-1 (inside the touch but never crossing fair)

VELVETFRUIT_EXTRACT  same three optimizations, fair = volume-weighted mid
                of the full depth (the "characteristic equation" of the
                book — what the bid/ask volumes imply the equilibrium is).

VEV_*           Same three optimizations on Black-Scholes fair:
                    fair_t = BS(S = VFE_microprice_t, K, T = ticks_to_expiry,
                                sigma_K = calibrated per-strike smile vol)
                Mean reversion is implicit: BS fair = arb-free anchor, and
                fair-value-taking automatically buys/sells when the market
                mid drifts away from BS by >= 1 tick.

No flow signals, no Stoikov skew, no IV scalp — those are in v02/v03 and
empirically didn't add edge at limit=100. Parameter budget: 1 (TAKE_WIDTH).
Volatility-smile constants are calibrated, not tuned.
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
# Logger — IMC P4 Visualizer compatible
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
# Black-Scholes (pure Python — scipy unavailable in submission env)
# =========================================================================


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S, K, T, sigma):
    if T <= 0 or sigma <= 0 or S <= 0:
        return max(S - K, 0.0)
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    return S * _norm_cdf(d1) - K * _norm_cdf(d2)


# =========================================================================
# Constants
# =========================================================================

POSITION_LIMITS = {
    "HYDROGEL_PACK": 100, "VELVETFRUIT_EXTRACT": 100,
    "VEV_4000": 100, "VEV_4500": 100, "VEV_5000": 100, "VEV_5100": 100,
    "VEV_5200": 100, "VEV_5300": 100, "VEV_5400": 100, "VEV_5500": 100,
    "VEV_6000": 100, "VEV_6500": 100,
}

# IMC-published true fair value for HP (per user instruction).
HP_FAIR = 10_000

# Volatility smile (median IV per strike, fitted from 3-day round 3 data).
SIGMA_SMILE = {
    4000: 0.0008960, 4500: 0.0004921, 5000: 0.0002616, 5100: 0.0002558,
    5200: 0.0002671, 5300: 0.0002705, 5400: 0.0002515, 5500: 0.0002697,
}
TRADED_STRIKES = [4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500]
T_EXPIRY = 30_000
TICK_STEP = 100

# Single tunable for v04 MM: how far below fair to call an offer "fair value taking".
TAKE_WIDTH = 0  # 0 = cross only when strictly cheaper than fair

# v05: informed-trader signal on VFE (mined from trade-size distribution).
INFORMED_SIZE_VFE = 11   # qty threshold for "big informed VFE trade"
INFORMED_DECAY = 0.998   # per-tick decay (~500-tick half-life)
INFORMED_GAIN = 6        # target-position lots per signed signal unit


# =========================================================================
# Book helpers
# =========================================================================


def microprice(depth: OrderDepth) -> float:
    bb = max(depth.buy_orders); ba = min(depth.sell_orders)
    bv = depth.buy_orders[bb]; av = -depth.sell_orders[ba]
    tot = bv + av
    return (bb * av + ba * bv) / tot if tot > 0 else (bb + ba) / 2.0


def full_depth_mid(depth: OrderDepth) -> float:
    """Volume-weighted full-depth mid — VFE's adaptive fair."""
    bv_total = sum(depth.buy_orders.values())
    av_total = sum(-v for v in depth.sell_orders.values())
    if bv_total <= 0 or av_total <= 0:
        return (max(depth.buy_orders) + min(depth.sell_orders)) / 2
    bid_vwap = sum(p * v for p, v in depth.buy_orders.items()) / bv_total
    ask_vwap = sum(p * (-v) for p, v in depth.sell_orders.items()) / av_total
    return (bid_vwap + ask_vwap) / 2


# =========================================================================
# The market-making engine — three P3 optimizations in one function
# =========================================================================


def market_make(symbol: str, depth: OrderDepth, position: int, fair: float) -> list[Order]:
    """Apply Chris's three P3 optimizations:
        1. Fair value taking
        2. Position reducing at fair value
        3. Penny jumping
    """
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    limit = POSITION_LIMITS.get(symbol, 50)
    fair_int = int(round(fair))
    out: list[Order] = []
    bv = sv = 0  # cumulative bought / sold this tick

    # 1. FAIR VALUE TAKING — cross any ask below fair, any bid above fair.
    for ask in sorted(depth.sell_orders):
        if ask >= fair - TAKE_WIDTH:
            break
        avail = -depth.sell_orders[ask]
        qty = min(avail, limit - position - bv)
        if qty <= 0:
            break
        out.append(Order(symbol, ask, qty)); bv += qty
    for bid in sorted(depth.buy_orders, reverse=True):
        if bid <= fair + TAKE_WIDTH:
            break
        avail = depth.buy_orders[bid]
        qty = min(avail, limit + position - sv)
        if qty <= 0:
            break
        out.append(Order(symbol, bid, -qty)); sv += qty

    # 2. POSITION REDUCING AT FAIR VALUE — take exactly-at-fair orders that
    #    move us toward zero. Free risk reduction; the order leaves the book
    #    so the next aggressor pays our wider quote instead of consuming it.
    pos_after = position + bv - sv
    if pos_after > 0 and fair_int in depth.buy_orders:
        avail = depth.buy_orders[fair_int]
        qty = min(avail, pos_after, limit + position - sv)
        if qty > 0:
            out.append(Order(symbol, fair_int, -qty)); sv += qty
    if pos_after < 0 and fair_int in depth.sell_orders:
        avail = -depth.sell_orders[fair_int]
        qty = min(avail, -pos_after, limit - position - bv)
        if qty > 0:
            out.append(Order(symbol, fair_int, qty)); bv += qty

    # 3. PENNY JUMPING — post inside the touch but never cross fair.
    best_bid = max(depth.buy_orders)
    best_ask = min(depth.sell_orders)
    # bid one tick above the best bid, but cap at fair-1 (never quote at-or-above fair)
    bid_px = min(best_bid + 1, fair_int - 1)
    ask_px = max(best_ask - 1, fair_int + 1)
    if bid_px < ask_px:
        buy_q = limit - position - bv
        sell_q = limit + position - sv
        if buy_q > 0:
            out.append(Order(symbol, bid_px, buy_q))
        if sell_q > 0:
            out.append(Order(symbol, ask_px, -sell_q))
    return out


# =========================================================================
# Informed-flow signal (VFE)
# =========================================================================


def update_informed_signal(store, market_trades_vfe, vfe_bid, vfe_ask):
    """Decay then add new big-trade prints. Returns the live signed signal."""
    sig = store.get("_inf", 0.0) * INFORMED_DECAY
    for t in market_trades_vfe or []:
        if t.quantity < INFORMED_SIZE_VFE:
            continue
        # Classify direction by price vs TOB.
        if t.price >= vfe_ask:
            sig += t.quantity   # informed buy
        elif t.price <= vfe_bid:
            sig -= t.quantity   # informed sell
    store["_inf"] = sig
    return sig


def vfe_orders_with_target(symbol, depth, position, fair, target):
    """v04 MM but with a position-targeting bias: we shade quote sizes
    toward `target` and may cross more aggressively when far from it."""
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    limit = POSITION_LIMITS[symbol]
    fair_int = int(round(fair))
    out: list[Order] = []
    bv = sv = 0

    # Aggressive take to chase target — pay up to TAKE_WIDTH ticks above fair
    # in the direction of the target.
    delta = target - position
    if delta > 0:  # need to buy
        for ask in sorted(depth.sell_orders):
            if ask > fair + 1:  # cross at most 1 tick above fair when chasing
                break
            avail = -depth.sell_orders[ask]
            qty = min(avail, delta - bv, limit - position - bv)
            if qty <= 0:
                break
            out.append(Order(symbol, ask, qty)); bv += qty
    elif delta < 0:  # need to sell
        need = -delta
        for bid in sorted(depth.buy_orders, reverse=True):
            if bid < fair - 1:
                break
            avail = depth.buy_orders[bid]
            qty = min(avail, need - sv, limit + position - sv)
            if qty <= 0:
                break
            out.append(Order(symbol, bid, -qty)); sv += qty

    # Then v04's vanilla fair-take + position-reduce + penny-jump on remaining
    pos_after = position + bv - sv
    rest = market_make(symbol, depth, pos_after, fair)
    # Reindex rest's quote sizes against our updated bv/sv state by adjusting
    # the limit-relative cap inside market_make — but market_make already does
    # so against (position=pos_after), so just append.
    out.extend(rest)
    return out


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

        # --- HYDROGEL_PACK: known fair = 10,000 ---
        hp_depth = state.order_depths.get("HYDROGEL_PACK")
        if hp_depth:
            ors = market_make(
                "HYDROGEL_PACK", hp_depth,
                state.position.get("HYDROGEL_PACK", 0),
                fair=HP_FAIR,
            )
            if ors:
                orders["HYDROGEL_PACK"] = ors

        # --- VELVETFRUIT_EXTRACT: MM + informed-flow position target ---
        vfe_depth = state.order_depths.get("VELVETFRUIT_EXTRACT")
        vfe_micro = None
        if vfe_depth and vfe_depth.buy_orders and vfe_depth.sell_orders:
            vfe_fair = full_depth_mid(vfe_depth)
            vfe_micro = microprice(vfe_depth)
            vfe_bid = max(vfe_depth.buy_orders)
            vfe_ask = min(vfe_depth.sell_orders)
            sig = update_informed_signal(
                store, state.market_trades.get("VELVETFRUIT_EXTRACT", []),
                vfe_bid, vfe_ask,
            )
            vfe_pos = state.position.get("VELVETFRUIT_EXTRACT", 0)
            target = max(-POSITION_LIMITS["VELVETFRUIT_EXTRACT"],
                         min(POSITION_LIMITS["VELVETFRUIT_EXTRACT"],
                             int(round(sig * INFORMED_GAIN))))
            ors = vfe_orders_with_target(
                "VELVETFRUIT_EXTRACT", vfe_depth, vfe_pos, vfe_fair, target,
            )
            if ors:
                orders["VELVETFRUIT_EXTRACT"] = ors

        # --- VEV_*: BS-with-smile fair, same three optimizations ---
        if vfe_micro is not None:
            ttx = max(1.0, T_EXPIRY - state.timestamp / TICK_STEP)
            for K in TRADED_STRIKES:
                sym = f"VEV_{K}"
                depth = state.order_depths.get(sym)
                if not depth:
                    continue
                sigma_K = SIGMA_SMILE[K]
                fair = bs_call(vfe_micro, K, ttx, sigma_K)
                ors = market_make(
                    sym, depth, state.position.get(sym, 0), fair=fair,
                )
                if ors:
                    orders[sym] = ors

        trader_data = json.dumps(store)
        logger.flush(state, orders, 0, trader_data)
        return orders, 0, trader_data
