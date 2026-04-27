"""
Round 4 — z_take: pure z-score market-taker off a STATIC mean.

For each configured product, cfg["mean"] / cfg["sd"] are fixed constants
(no estimation, no EMA, no warmup). Each tick:

    mid = (best_bid + best_ask) / 2
    z   = (mid - mean) / sd

If z >= z_thresh: sell into the bid stack at prices >= mean, up to
take_size and remaining short capacity.
If z <= -z_thresh: buy from the ask stack at prices <= mean, up to
take_size and remaining long capacity.

No quotes, no MM, no persistent state — cross the spread when |z| is
large enough, sit out otherwise.
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

# mean / sd are the empirical mean and stdev of mid_price across all
# four observed days (round-3 day 0 + round-4 days 1-3, 40,000 ticks per
# product). VEV_6000 / VEV_6500 have sd=0 (mid pinned at 0.5) so they're
# excluded.
CFGS = [
    {"symbol": "HYDROGEL_PACK",       "mean": 9994, "sd": 32.588, "z_thresh": 1.0, "take_size": 17, "limit": 200},
    {"symbol": "VELVETFRUIT_EXTRACT", "mean": 5247, "sd": 17.091, "z_thresh": 1.0, "take_size": 17, "limit": 200},
    {"symbol": "VEV_4000",            "mean": 1247, "sd": 17.114, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_4500",            "mean":  747, "sd": 17.105, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_5000",            "mean":  252, "sd": 16.381, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_5100",            "mean":  163, "sd": 15.327, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_5200",            "mean":   91, "sd": 12.796, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_5300",            "mean":   43, "sd":  8.976, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_5400",            "mean":   14, "sd":  4.608, "z_thresh": 1.0, "take_size": 17, "limit": 300},
    {"symbol": "VEV_5500",            "mean":    6, "sd":  2.477, "z_thresh": 1.0, "take_size": 17, "limit": 300},
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

def _z_take_orders(state, cfg):
    sym = cfg["symbol"]
    depth = state.order_depths.get(sym)
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    mid = (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0
    mean, sd = cfg["mean"], cfg["sd"]
    if sd <= 0:
        return []
    z = (mid - mean) / sd
    if abs(z) < cfg["z_thresh"]:
        return []

    pos = state.position.get(sym, 0)
    limit = cfg["limit"]
    take_size = cfg["take_size"]

    if z > 0:
        # Mid above mean: sell into bids at prices >= mean.
        room = max(0, min(take_size, limit + pos))
        if room <= 0:
            return []
        orders, _ = _walk_book(depth, -1, sym, lambda px: px >= mean, room)
        return orders

    # Mid below mean: buy from asks at prices <= mean.
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
        orders: dict[str, list[Order]] = {}
        for cfg in CFGS:
            ors = _z_take_orders(state, cfg)
            if ors:
                orders[cfg["symbol"]] = ors
        return orders, 0, ""
