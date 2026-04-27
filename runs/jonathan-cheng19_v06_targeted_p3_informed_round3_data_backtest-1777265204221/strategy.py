"""Round 3 v06 — clean P3 MM + (fair-mid) position-targeting + VFE informed-flow.

Synthesises the lessons of v01-v05:
  - v04 (clean P3 MM) is too passive — penny-jumping at fair captures only
    spread, leaving the structural mean-reversion edge on HP/VFE on the table.
    Result: 62k vs v03's 127k.
  - v01-v03's Kalman MR captures that edge but with Kalman fair_static
    hand-tuning, mr_gain=2000 that drives drawdown >35k on day 0.
  - v05's informed-flow position TARGETING (90-lot bet on a single size-15
    print) is too aggressive — over-trades a small sample.

v06 blends them with the smallest effective doses:

1. Clean P3 MM (v04) — fair-take + position-reduce-at-fair + penny-jump.
2. Position-targeting (replaces Kalman MR) — target_t = clip(GAIN_TGT *
   (fair - mid), -limit, +limit). Uses *current* fair (10000 for HP, the
   VFE characteristic equation) not a hand-picked anchor. One param: GAIN_TGT.
3. VFE informed-flow ADDITION to the target — for big VFE prints (size>=11),
   add sign(side) * INFORMED_GAIN to the target. Smaller GAIN than v05 so
   no single print monopolises capacity. One param: INFORMED_GAIN_S.
4. VEV options — pure v04 MM on BS-with-smile fair. No extra signal.

The whole thing is parameterised by 5 numbers (TAKE_WIDTH, GAIN_TGT,
INFORMED_SIZE_VFE, INFORMED_GAIN_S, INFORMED_DECAY). The 8 sigma_K
constants are calibrated, not tuned.
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
# Black-Scholes (pure Python)
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

HP_FAIR = 10_000

SIGMA_SMILE = {
    4000: 0.0008960, 4500: 0.0004921, 5000: 0.0002616, 5100: 0.0002558,
    5200: 0.0002671, 5300: 0.0002705, 5400: 0.0002515, 5500: 0.0002697,
}
TRADED_STRIKES = [4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500]
T_EXPIRY = 30_000
TICK_STEP = 100

# Tunables — five numbers total.
TAKE_WIDTH = 0
GAIN_TGT = 5            # target_pos = GAIN_TGT * (fair - mid), clipped
INFORMED_SIZE_VFE = 11
INFORMED_GAIN_S = 2     # smaller than v05's 6 to avoid single-print blowups
INFORMED_DECAY = 0.998


# =========================================================================
# Book helpers
# =========================================================================


def microprice(depth):
    bb = max(depth.buy_orders); ba = min(depth.sell_orders)
    bv = depth.buy_orders[bb]; av = -depth.sell_orders[ba]
    tot = bv + av
    return (bb * av + ba * bv) / tot if tot > 0 else (bb + ba) / 2.0


def full_depth_mid(depth):
    bv_total = sum(depth.buy_orders.values())
    av_total = sum(-v for v in depth.sell_orders.values())
    if bv_total <= 0 or av_total <= 0:
        return (max(depth.buy_orders) + min(depth.sell_orders)) / 2
    bid_vwap = sum(p * v for p, v in depth.buy_orders.items()) / bv_total
    ask_vwap = sum(p * (-v) for p, v in depth.sell_orders.items()) / av_total
    return (bid_vwap + ask_vwap) / 2


# =========================================================================
# Targeted P3 MM — fair-take + position-reduce + penny-jump + soft target
# =========================================================================


def market_make_targeted(symbol, depth, position, fair, target=None):
    """v04's three P3 optimizations PLUS:
       - if `target` given and != 0: aggressive cross to chase target up to
         1 tick beyond fair (capped by the limit)
    """
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    limit = POSITION_LIMITS.get(symbol, 50)
    fair_int = int(round(fair))
    out: list[Order] = []
    bv = sv = 0

    # 0. TARGETING (chase) — only if target meaningfully different from position.
    if target is not None:
        delta = target - position
        if delta > 0:
            for ask in sorted(depth.sell_orders):
                if ask > fair + 1:
                    break
                avail = -depth.sell_orders[ask]
                qty = min(avail, delta - bv, limit - position - bv)
                if qty <= 0:
                    break
                out.append(Order(symbol, ask, qty)); bv += qty
        elif delta < 0:
            need = -delta
            for bid in sorted(depth.buy_orders, reverse=True):
                if bid < fair - 1:
                    break
                avail = depth.buy_orders[bid]
                qty = min(avail, need - sv, limit + position - sv)
                if qty <= 0:
                    break
                out.append(Order(symbol, bid, -qty)); sv += qty

    # 1. FAIR VALUE TAKING (any price strictly better than fair).
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

    # 2. POSITION REDUCING AT FAIR.
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

    # 3. PENNY JUMPING.
    best_bid = max(depth.buy_orders)
    best_ask = min(depth.sell_orders)
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
# Informed-flow signal (VFE only)
# =========================================================================


def update_informed_signal(store, market_trades_vfe, vfe_bid, vfe_ask):
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

        # --- HYDROGEL_PACK: fair=10000, target = GAIN_TGT*(fair-mid) ---
        hp_depth = state.order_depths.get("HYDROGEL_PACK")
        if hp_depth and hp_depth.buy_orders and hp_depth.sell_orders:
            hp_pos = state.position.get("HYDROGEL_PACK", 0)
            hp_mid = (max(hp_depth.buy_orders) + min(hp_depth.sell_orders)) / 2
            limit = POSITION_LIMITS["HYDROGEL_PACK"]
            target = max(-limit, min(limit, int(round(GAIN_TGT * (HP_FAIR - hp_mid)))))
            ors = market_make_targeted("HYDROGEL_PACK", hp_depth, hp_pos, HP_FAIR, target)
            if ors:
                orders["HYDROGEL_PACK"] = ors

        # --- VFE: adaptive fair, target from (fair-mid) + informed flow ---
        vfe_depth = state.order_depths.get("VELVETFRUIT_EXTRACT")
        vfe_micro = None
        if vfe_depth and vfe_depth.buy_orders and vfe_depth.sell_orders:
            vfe_fair = full_depth_mid(vfe_depth)
            vfe_micro = microprice(vfe_depth)
            vfe_bid = max(vfe_depth.buy_orders)
            vfe_ask = min(vfe_depth.sell_orders)
            vfe_mid = (vfe_bid + vfe_ask) / 2
            sig = update_informed_signal(
                store, state.market_trades.get("VELVETFRUIT_EXTRACT", []),
                vfe_bid, vfe_ask,
            )
            limit = POSITION_LIMITS["VELVETFRUIT_EXTRACT"]
            tgt_price = GAIN_TGT * (vfe_fair - vfe_mid)
            tgt_inf = INFORMED_GAIN_S * sig
            target = max(-limit, min(limit, int(round(tgt_price + tgt_inf))))
            vfe_pos = state.position.get("VELVETFRUIT_EXTRACT", 0)
            ors = market_make_targeted("VELVETFRUIT_EXTRACT", vfe_depth, vfe_pos, vfe_fair, target)
            if ors:
                orders["VELVETFRUIT_EXTRACT"] = ors

        # --- VEV options: pure v04 MM on BS-with-smile fair ---
        if vfe_micro is not None:
            ttx = max(1.0, T_EXPIRY - state.timestamp / TICK_STEP)
            for K in TRADED_STRIKES:
                sym = f"VEV_{K}"
                depth = state.order_depths.get(sym)
                if not depth:
                    continue
                sigma_K = SIGMA_SMILE[K]
                fair = bs_call(vfe_micro, K, ttx, sigma_K)
                ors = market_make_targeted(sym, depth, state.position.get(sym, 0), fair, target=None)
                if ors:
                    orders[sym] = ors

        trader_data = json.dumps(store)
        logger.flush(state, orders, 0, trader_data)
        return orders, 0, trader_data
