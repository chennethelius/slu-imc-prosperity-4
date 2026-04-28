"""
Round 4 — z_take_imb (TMP): z_take + order-book imbalance fade.

Empirical finding (full-day correlations on round4 d1-d3 of forward mid
change vs current order-book imbalance):

  Symbol         Δt=100    Δt=500
  VEV_4000      -0.48      -0.29
  HP            -0.33      -0.16
  VFE           -0.32      -0.17
  VEV_5300      -0.07      -0.05
  VEV_5400/5500  ~0         ~0

Heavy bid volume predicts mid down; heavy ask volume predicts mid up.
This is a fade-imbalance signal: depth on one side reflects stale orders
about to be picked off, not directional pressure.

Implementation — bias the static z by the normalized imbalance:

  imb = (sum_bid_vol - sum_ask_vol) / (sum_bid_vol + sum_ask_vol)
  z'  = (mid - mean) / sd + k_imb * imb

If imb < 0 (ask-heavy → bullish): z' < z → harder to sell, easier to buy.
If imb > 0 (bid-heavy → bearish): z' > z → harder to buy, easier to sell.

Only the "big-3" products (HP, VFE, VEV_4000) get the imbalance overlay
since the deep OTM strikes have negligible imb-vs-fwd correlation. Take
filter and limits unchanged from z_take baseline.
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
DEFAULT_K_IMB = 0.75   # bias added to z-score
DEFAULT_IMB_THRESH = 0.7  # |imb| above this → standalone fade-take fires
DEFAULT_IMB_TAKE_SIZE = 25  # take_size for the standalone trigger

# imb_on=True: include the imbalance bias for this symbol.
CFGS = [
    {"symbol": "HYDROGEL_PACK",       "mean": 9994, "sd": 32.588, "z_thresh": 1.0, "take_size": 17, "limit": 200, "imb_on": True},
    {"symbol": "VELVETFRUIT_EXTRACT", "mean": 5247, "sd": 17.091, "z_thresh": 1.0, "take_size": 17, "limit": 200, "imb_on": True},
    {"symbol": "VEV_4000",            "mean": 1247, "sd": 17.114, "z_thresh": 1.0, "take_size": 17, "limit": 300, "imb_on": True},
    {"symbol": "VEV_4500",            "mean":  747, "sd": 17.105, "z_thresh": 1.0, "take_size": 17, "limit": 300, "imb_on": False},
    {"symbol": "VEV_5000",            "mean":  252, "sd": 16.381, "z_thresh": 1.0, "take_size": 17, "limit": 300, "imb_on": False},
    {"symbol": "VEV_5100",            "mean":  163, "sd": 15.327, "z_thresh": 1.0, "take_size": 17, "limit": 300, "imb_on": False},
    {"symbol": "VEV_5200",            "mean":   91, "sd": 12.796, "z_thresh": 1.0, "take_size": 17, "limit": 300, "imb_on": False},
    {"symbol": "VEV_5300",            "mean":   43, "sd":  8.976, "z_thresh": 1.0, "take_size": 17, "limit": 300, "imb_on": False},
    {"symbol": "VEV_5400",            "mean":   14, "sd":  4.608, "z_thresh": 1.0, "take_size": 17, "limit": 300, "imb_on": False},
    {"symbol": "VEV_5500",            "mean":    6, "sd":  2.477, "z_thresh": 1.0, "take_size": 17, "limit": 300, "imb_on": False},
]


def _imbalance(depth):
    """Total bid volume minus total ask volume, normalised to [-1, 1]."""
    bv = sum(abs(v) for v in depth.buy_orders.values())
    av = sum(abs(v) for v in depth.sell_orders.values())
    if bv + av <= 0:
        return 0.0
    return (bv - av) / (bv + av)


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
    pos = state.position.get(sym, 0)
    limit = cfg["limit"]
    take_size = cfg["take_size"]

    imb = _imbalance(depth) if cfg.get("imb_on") else 0.0
    z_biased = z + DEFAULT_K_IMB * imb

    out = []
    bv = sv = 0

    # Primary z-take (using imbalance-biased z)
    if abs(z_biased) >= cfg["z_thresh"]:
        if z_biased > 0:
            room = max(0, min(take_size, limit + pos - sv))
            if room > 0:
                ords, filled = _walk_book(depth, -1, sym, lambda px: px >= mean, room)
                out.extend(ords); sv += filled
        else:
            room = max(0, min(take_size, limit - pos - bv))
            if room > 0:
                ords, filled = _walk_book(depth, +1, sym, lambda px: px <= mean, room)
                out.extend(ords); bv += filled

    # Standalone imbalance fade — fires even when |z|<thresh, on the big-3
    # only. Bid-heavy book → fade by selling; ask-heavy → fade by buying.
    # No mean filter (imbalance signal is mostly Δt=100 not mean-revert).
    if cfg.get("imb_on") and abs(imb) >= DEFAULT_IMB_THRESH:
        size = DEFAULT_IMB_TAKE_SIZE
        if imb > 0:  # bid-heavy → mid expected down → SELL
            room = max(0, min(size, limit + pos - sv))
            if room > 0:
                ords, filled = _walk_book(depth, -1, sym, lambda px: True, room)
                out.extend(ords); sv += filled
        else:        # ask-heavy → mid expected up → BUY
            room = max(0, min(size, limit - pos - bv))
            if room > 0:
                ords, filled = _walk_book(depth, +1, sym, lambda px: True, room)
                out.extend(ords); bv += filled

    return out


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
