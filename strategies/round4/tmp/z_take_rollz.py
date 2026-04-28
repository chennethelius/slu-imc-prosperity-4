"""
Round 4 — z_take_rollz (TMP): z_take with rolling EWMA z-score.

z_take baseline uses a STATIC mean / sd computed offline from 40,000 ticks.
This variant replaces them with rolling EWMA estimates so the strategy
adapts to local drift without a fitted prior.

Each tick:
  mean_t = (1-α)·mean_{t-1} + α·mid_t
  var_t  = (1-α)·var_{t-1}  + α·(mid_t - mean_{t-1})²
  sd_t   = sqrt(var_t)
  z_t    = (mid_t - mean_t) / sd_t

Effective window is roughly 2/α ticks. ALPHA=0.004 → ~500-tick half-life.
WARMUP suppresses entries until the EWMA stabilizes (otherwise the first
few ticks have artificially small variance and any wiggle prints |z|>>1).

Take logic and per-product limits are otherwise identical to z_take.py.

Hypothesis: if mid drifts day-to-day (so the static cfg["mean"] is stale
on any given day), a rolling estimator should fire more correctly. The
counter-hypothesis is that the static long-run mean is a real anchor that
mid actually mean-reverts to, in which case rolling is strictly worse.
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
DEFAULT_ALPHA = 0.004
DEFAULT_WARMUP = 1000

CFGS = [
    {"symbol": "HYDROGEL_PACK",       "mean_init": 9994, "sd_init": 32.588, "z_thresh": 1.0, "take_size": 17, "limit": 200},
    {"symbol": "VELVETFRUIT_EXTRACT", "mean_init": 5247, "sd_init": 17.091, "z_thresh": 1.0, "take_size": 17, "limit": 200},
    {"symbol": "VEV_4000",            "mean_init": 1247, "sd_init": 17.114, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_4500",            "mean_init":  747, "sd_init": 17.105, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_5000",            "mean_init":  252, "sd_init": 16.381, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_5100",            "mean_init":  163, "sd_init": 15.327, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_5200",            "mean_init":   91, "sd_init": 12.796, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_5300",            "mean_init":   43, "sd_init":  8.976, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_5400",            "mean_init":   14, "sd_init":  4.608, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_5500",            "mean_init":    6, "sd_init":  2.477, "z_thresh": 1.0, "take_size": 17, "limit": 300},
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

    # Rolling EWMA mean / variance. Update before reading z so the trade
    # decision uses the post-update state (consistent with how no_marks
    # treats _conviction's _var update).
    alpha = DEFAULT_ALPHA
    n = store.get(f"_{sym}_n", 0) + 1
    mean_prev = store.get(f"_{sym}_m", float(cfg["mean_init"]))
    var_prev = store.get(f"_{sym}_v", float(cfg["sd_init"]) ** 2)
    dev = mid - mean_prev
    mean = (1.0 - alpha) * mean_prev + alpha * mid
    var = (1.0 - alpha) * var_prev + alpha * dev * dev
    store[f"_{sym}_n"] = n
    store[f"_{sym}_m"] = mean
    store[f"_{sym}_v"] = var

    if n < DEFAULT_WARMUP:
        return []

    sd = var ** 0.5
    if sd <= 0:
        return []
    z = (mid - mean) / sd
    if abs(z) < cfg["z_thresh"]:
        return []

    pos = state.position.get(sym, 0)
    limit = cfg["limit"]
    take_size = cfg["take_size"]

    if z > 0:
        room = max(0, min(take_size, limit + pos))
        if room <= 0:
            return []
        orders, _ = _walk_book(depth, -1, sym, lambda px: px >= mean, room)
        return orders

    room = max(0, min(take_size, limit - pos))
    if room <= 0:
        return []
    orders, _ = _walk_book(depth, +1, sym, lambda px: px <= mean, room)
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
