"""
Round 4 — z_take_rollbias (TMP): z_take + rolling-mean drift correction.

Decomposes the z-score into a rolling local component and a static-anchor
correction term, both normalized by the static sd:

  z_local  = (mid - rolling_mean) / static_sd
  drift_z  = (rolling_mean - static_mean) / static_sd
  z        = z_local + k * drift_z

Algebra:
  k=0: z = (mid - rolling_mean) / static_sd      (rolling, fails alone)
  k=1: z = (mid - static_mean)  / static_sd      (= z_take baseline, exact)
  0<k<1: partial drift correction — under-weights the static anchor

Equivalently: z = (mid - (k·static_mean + (1-k)·rolling_mean)) / static_sd,
i.e. a static-sd z-score against a fair value that sits (1-k) of the way
from static toward rolling. k slightly under 1 = the fair gets a small
nudge toward the recent local average.

Take filter uses STATIC_MEAN as the price gate (only sell ≥ static, only
buy ≤ static) regardless of where rolling drifted.

Tuned config (α=0.0025 EWMA, k=0.95) on round4 d1-d3 at QP=1.0:
                    z_take baseline       z_take_rollbias
  d1                 261,612               260,128
  d2                 205,530               206,005   (+475 → new min)
  d3                 266,827               273,981   (+7,154)
  mean               244,656               246,705   (+2,049)
  min                205,530               206,005   (+475)
  mean+min           450,186               452,710   (+2,524, +0.56%)

Strict Pareto improvement on (mean, min). QP=0.0 produces identical
numbers (take-only ⇒ QP-invariant), so the winner is submission-stable.
"""

import json
from typing import Any
from datamodel import Listing, Observation, Order, OrderDepth, ProsperityEncoder, Symbol, Trade, TradingState



class Logger:
    def __init__(self) -> None:
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects: Any, sep: str = " ", end: str = "\n") -> None:
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state: TradingState, orders: dict[Symbol, list[Order]], conversions: int, trader_data: str) -> None:
        base_length = len(self.to_json([self.compress_state(state, ""), self.compress_orders(orders), conversions, "", ""]))
        max_item_length = (self.max_log_length - base_length) // 3
        print(self.to_json([
            self.compress_state(state, self.truncate(state.traderData, max_item_length)),
            self.compress_orders(orders),
            conversions,
            self.truncate(trader_data, max_item_length),
            self.truncate(self.logs, max_item_length),
        ]))
        self.logs = ""

    def compress_state(self, state: TradingState, trader_data: str) -> list[Any]:
        return [state.timestamp, trader_data, self.compress_listings(state.listings),
                self.compress_order_depths(state.order_depths), self.compress_trades(state.own_trades),
                self.compress_trades(state.market_trades), state.position, self.compress_observations(state.observations)]

    def compress_listings(self, listings):
        return [[l.symbol, l.product, l.denomination] for l in listings.values()]

    def compress_order_depths(self, order_depths):
        return {s: [od.buy_orders, od.sell_orders] for s, od in order_depths.items()}

    def compress_trades(self, trades):
        return [[t.symbol, t.price, t.quantity, t.buyer, t.seller, t.timestamp]
                for arr in trades.values() for t in arr]

    def compress_observations(self, observations: Observation) -> list[Any]:
        conversion_observations = {}
        for product, obs in observations.conversionObservations.items():
            conversion_observations[product] = [
                obs.bidPrice, obs.askPrice, obs.transportFees,
                obs.exportTariff, obs.importTariff, obs.sugarPrice, obs.sunlightIndex,
            ]
        return [observations.plainValueObservations, conversion_observations]

    def compress_orders(self, orders):
        return [[o.symbol, o.price, o.quantity] for arr in orders.values() for o in arr]

    def to_json(self, value: Any) -> str:
        return json.dumps(value, cls=ProsperityEncoder, separators=(",", ":"))

    def truncate(self, value: str, max_length: int) -> str:
        lo, hi = 0, min(len(value), max_length)
        out = ""
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = value[:mid]
            if len(candidate) < len(value):
                candidate += "..."
            if len(json.dumps(candidate)) <= max_length:
                out = candidate; lo = mid + 1
            else:
                hi = mid - 1
        return out


logger = Logger()

# ============================================================================
# Per-product config
# ============================================================================

# Rolling EWMA parameters. ALPHA=0.004 → ~500-tick half-life. WARMUP
# suppresses trading for the first WARMUP ticks so the EWMA stabilises.
# mean_init / sd_init seed the EWMA with the historical static values
# (kept for reference; once warmed up they stop mattering).
DEFAULT_ALPHA = 0.0025  # ~2000-tick half-life
DEFAULT_WARMUP = 0
DEFAULT_K = 0.95        # bias strength: 0=pure rolling, 1=exact static, >1=over-anchor

# static_mean / static_sd are the historical anchors from 40,000 ticks
# (round-3 d0 + round-4 d1-3). They drive both the bias term and the
# take-price filter.
CFGS = [
    {"symbol": "HYDROGEL_PACK",       "static_mean": 9994, "static_sd": 32.588, "z_thresh": 1.0, "take_size": 17, "limit": 200},
    {"symbol": "VELVETFRUIT_EXTRACT", "static_mean": 5247, "static_sd": 17.091, "z_thresh": 1.0, "take_size": 17, "limit": 200},
    {"symbol": "VEV_4000",            "static_mean": 1247, "static_sd": 17.114, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_4500",            "static_mean":  747, "static_sd": 17.105, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_5000",            "static_mean":  252, "static_sd": 16.381, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_5100",            "static_mean":  163, "static_sd": 15.327, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_5200",            "static_mean":   91, "static_sd": 12.796, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_5300",            "static_mean":   43, "static_sd":  8.976, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_5400",            "static_mean":   14, "static_sd":  4.608, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_5500",            "static_mean":    6, "static_sd":  2.477, "z_thresh": 1.0, "take_size": 17, "limit": 300},
]


# ============================================================================
# Book walker — fill against the resting book on `side` at prices
# matching `ok(px)`, up to qty_target. side=+1 hits asks (buy); side=-1
# hits bids (sell).
# ============================================================================

def _walk_book(depth, side, sym, ok, qty_target):
    if side > 0:
        prices = sorted(depth.sell_orders)
        book = depth.sell_orders
    else:
        prices = sorted(depth.buy_orders, reverse=True)
        book = depth.buy_orders
    out, filled = [], 0
    for px in prices:
        if filled >= qty_target or not ok(px):
            break
        qty = min(abs(book[px]), qty_target - filled)
        if qty <= 0:
            break
        out.append(Order(sym, px, side * qty))
        filled += qty
    return out, filled


# ============================================================================
# Per-product z-take
# ============================================================================

def _z_take_orders(state, cfg, store):
    sym = cfg["symbol"]
    depth = state.order_depths.get(sym)
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    mid = (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0

    static_mean = float(cfg["static_mean"])
    static_sd = float(cfg["static_sd"])

    # Rolling EWMA mean / variance, seeded from the static anchors.
    alpha = DEFAULT_ALPHA
    n = store.get(f"_{sym}_n", 0) + 1
    mean_prev = store.get(f"_{sym}_m", static_mean)
    var_prev = store.get(f"_{sym}_v", static_sd ** 2)
    dev = mid - mean_prev
    rolling_mean = (1.0 - alpha) * mean_prev + alpha * mid
    rolling_var = (1.0 - alpha) * var_prev + alpha * dev * dev
    store[f"_{sym}_n"] = n
    store[f"_{sym}_m"] = rolling_mean
    store[f"_{sym}_v"] = rolling_var

    if n < DEFAULT_WARMUP:
        return []

    rolling_sd = rolling_var ** 0.5
    if rolling_sd <= 0 or static_sd <= 0:
        return []

    # z_local = local mean-rev (relative to rolling mean / rolling sd)
    # drift_z = how far the rolling mean has drifted from static mean
    # z_biased = z_local + k * drift_z   pulls the signal toward static
    # Using static_sd for both terms so k=1 exactly recovers z_static.
    z_local = (mid - rolling_mean) / static_sd
    drift_z = (rolling_mean - static_mean) / static_sd
    z = z_local + DEFAULT_K * drift_z

    if abs(z) < cfg["z_thresh"]:
        return []

    pos = state.position.get(sym, 0)
    limit = cfg["limit"]
    take_size = cfg["take_size"]

    # Take filter uses STATIC mean — never enter at prices on the wrong
    # side of long-run fair regardless of where rolling drifted.
    if z > 0:
        room = max(0, min(take_size, limit + pos))
        if room <= 0:
            return []
        orders, _ = _walk_book(depth, -1, sym, lambda px: px >= static_mean, room)
        return orders

    room = max(0, min(take_size, limit - pos))
    if room <= 0:
        return []
    orders, _ = _walk_book(depth, +1, sym, lambda px: px <= static_mean, room)
    return orders


# ============================================================================
# Trader
# ============================================================================

class Trader:
    def bid(self):
        return 0

    def run(self, state: TradingState):
        try:
            store = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            store = {}
        orders: dict[str, list[Order]] = {}
        for cfg in CFGS:
            ors = _z_take_orders(state, cfg, store)
            if ors:
                orders[cfg["symbol"]] = ors
        return orders, 0, json.dumps(store)
