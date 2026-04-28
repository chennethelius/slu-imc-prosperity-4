"""
Round 4 — z_take_m55_vfe (TMP) — FAILED EXPERIMENT, kept for record.

Hypothesis: Mark 55 on VFE has -$2.5 alpha both sides (mark_alpha.py),
~400 prints/day. Adding fade-direction gating to z_take's VFE entries
should suppress losing entries against the signal.

Mechanism — directional TTL fade signal on VFE only:
  Mark 55 sells VFE → up_signal   (mid expected to rise)
  Mark 55 buys  VFE → down_signal (mid expected to drop)

Result vs z_take baseline (round4 d1-d3, QP=1.0):
  d1: 261,612 → 261,612  (Δ=0)        — gating never altered any orders
  d2: 205,530 → 205,530  (Δ=0)        — gating never altered any orders
  d3: 266,827 → 252,976  (Δ=-13,851)  — suppressed profitable VFE sells
  mean 244,656 → 240,039 (Δ=-4,617),  min unchanged

Why it didn't help:
  Mark 55 hits the prevailing bid/ask, which by definition pushes mid
  AWAY from the static mean. So Mark 55's fade signal direction always
  aligns with z_take's mean-revert direction (Mark 55 sold → mid is
  below mean → z<0 → z_take buys → boost flag fires, no-op). Gating
  only differs from baseline when the TTL persists past a regime change;
  on d3 (ts 880000-881600) lingering up_signal suppressed a sequence of
  profitable z>0 sells.

Conclusion across all three round-4 mark-signal angles attempted:
  1. HP/VFE/VEV_4000 mark-flow takes (marks_overlay.py): catastrophic
     because Mark 14/38 are paired MMs.
  2. Mark 22 sell-fade as quote intercept (quote_tighter.py): +0.13%
     marginal win on no_marks.
  3. Mark 55 VFE fade gating on z_take (this file): zero or negative.

The implicit alpha in Mark counterparty data is already captured by
mean-reversion against a static fair value. No incremental edge to
extract via flow-following or fade overlays on top of either baseline.
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

MF_TTL = 250
MF_SUPPRESS = 0.0  # take_size × this when adverse mark flow (0 = skip)
MF_BOOST = 1.0     # take_size × this when same-side mark flow (1 = unchanged)


def _z_take_orders(state, cfg, store):
    sym = cfg["symbol"]
    depth = state.order_depths.get(sym)
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    mid = (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0
    mean, sd = cfg["mean"], cfg["sd"]
    if sd <= 0:
        return []
    z = (mid - mean) / sd

    # Mark 55 fade signal on VFE only. Decay any prior TTL each tick, then
    # refresh from this tick's market trades.
    is_vfe = sym == "VELVETFRUIT_EXTRACT"
    up_ttl = max(0, store.get(f"_{sym}_up", 0) - 1)
    dn_ttl = max(0, store.get(f"_{sym}_dn", 0) - 1)
    if is_vfe:
        for t in state.market_trades.get(sym, []) or []:
            if getattr(t, "buyer", "") == "Mark 55":
                dn_ttl = MF_TTL  # noise buy → mid drops → bearish
            if getattr(t, "seller", "") == "Mark 55":
                up_ttl = MF_TTL  # noise sell → mid rises → bullish
    store[f"_{sym}_up"] = up_ttl
    store[f"_{sym}_dn"] = dn_ttl

    if abs(z) < cfg["z_thresh"]:
        return []

    pos = state.position.get(sym, 0)
    limit = cfg["limit"]
    base_take = cfg["take_size"]

    up_signal = up_ttl > 0
    down_signal = dn_ttl > 0

    if z > 0:
        # Would sell long. With Mark 55 fade: up_signal = adverse (suppress),
        # down_signal = aligned (boost).
        mult = (MF_SUPPRESS if (is_vfe and up_signal)
                else MF_BOOST if (is_vfe and down_signal)
                else 1.0)
        adj = max(0, int(round(base_take * mult)))
        if adj <= 0:
            return []
        room = max(0, min(adj, limit + pos))
        if room <= 0:
            return []
        orders, _ = _walk_book(depth, -1, sym, lambda px: px >= mean, room)
        return orders

    # z < 0 — would buy. up_signal = aligned (boost), down_signal = adverse.
    mult = (MF_SUPPRESS if (is_vfe and down_signal)
            else MF_BOOST if (is_vfe and up_signal)
            else 1.0)
    adj = max(0, int(round(base_take * mult)))
    if adj <= 0:
        return []
    room = max(0, min(adj, limit - pos))
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
