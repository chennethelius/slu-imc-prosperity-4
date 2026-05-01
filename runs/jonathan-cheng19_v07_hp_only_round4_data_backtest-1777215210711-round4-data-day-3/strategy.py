"""Round 4 v07 — ISOLATED HYDROGEL_PACK strategy.

Strips VFE and VEV options entirely. Only HP runs.

HP logic (matches user spec):
   Fair = 10000 (hard-coded, IMC-confirmed)
   1. Fair-value taking — cross any ask < 10000, any bid > 10000 (free PnL)
   2. Position-reducing-at-fair — take orders @ 10000 that move |position| → 0
   3. Always-on passive MM at best_bid+1 / best_ask-1 (capped fair±1)
   4. Mean-reversion target with light inventory skew (kept conservative):
        target = HP_MR_GAIN * (10000 - mid),  capped ±HP_SOFT_LIMIT
        deviation = position - target
        buy_qsize  = BASE * (1 - deviation/HP_LIMIT)
        sell_qsize = BASE * (1 + deviation/HP_LIMIT)

Two MR parameters (HP_MR_GAIN=2, HP_SOFT_LIMIT=60), kept low to avoid
overfit. The asymmetric quote sizes pull position toward the MR target
without ever crossing the spread.

Historical 3-day round 4 (v06 with same HP logic): HP per day = 29k/24k/18k,
Sharpe 7.10, drawdown 23k. Negative-skew flat-and-up curve.
"""

import json
from typing import Any

from datamodel import (
    Listing, Observation, Order, OrderDepth, ProsperityEncoder, Symbol, Trade, TradingState,
)


# === Logger (visualizer-compatible) =========================================
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


# === HP constants ===========================================================
HP_FAIR = 10000
HP_LIMIT = 200
HP_QSIZE = 30
HP_MR_GAIN = 2          # lots of position-target per tick of (fair - mid)
HP_SOFT_LIMIT = 60      # max desired position bias (30% of HP_LIMIT)


def hp_orders(state, store):
    depth = state.order_depths.get("HYDROGEL_PACK")
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []

    position = state.position.get("HYDROGEL_PACK", 0)
    out = []
    bv = sv = 0

    # 1. Fair-value taking — any ask < fair, any bid > fair (free PnL).
    for ask in sorted(depth.sell_orders):
        if ask >= HP_FAIR: break
        avail = -depth.sell_orders[ask]
        qty = min(avail, HP_LIMIT - position - bv)
        if qty <= 0: break
        out.append(Order("HYDROGEL_PACK", ask, qty)); bv += qty
    for bid in sorted(depth.buy_orders, reverse=True):
        if bid <= HP_FAIR: break
        avail = depth.buy_orders[bid]
        qty = min(avail, HP_LIMIT + position - sv)
        if qty <= 0: break
        out.append(Order("HYDROGEL_PACK", bid, -qty)); sv += qty

    # 2. Position-reducing at fair — take orders @ 10000 that move toward zero.
    pos_after = position + bv - sv
    if pos_after > 0 and HP_FAIR in depth.buy_orders:
        avail = depth.buy_orders[HP_FAIR]
        qty = min(avail, pos_after, HP_LIMIT + position - sv)
        if qty > 0:
            out.append(Order("HYDROGEL_PACK", HP_FAIR, -qty)); sv += qty
    if pos_after < 0 and HP_FAIR in depth.sell_orders:
        avail = -depth.sell_orders[HP_FAIR]
        qty = min(avail, -pos_after, HP_LIMIT - position - bv)
        if qty > 0:
            out.append(Order("HYDROGEL_PACK", HP_FAIR, qty)); bv += qty

    # 3. Mean-reversion target + inventory-skewed passive quotes.
    best_bid = max(depth.buy_orders); best_ask = min(depth.sell_orders)
    mid_now = (best_bid + best_ask) / 2.0
    target = max(-HP_SOFT_LIMIT, min(HP_SOFT_LIMIT,
                                     int(round(HP_MR_GAIN * (HP_FAIR - mid_now)))))
    bid_px = min(best_bid + 1, HP_FAIR - 1)
    ask_px = max(best_ask - 1, HP_FAIR + 1)
    if bid_px < ask_px:
        pos_after_take = position + bv - sv
        deviation = pos_after_take - target
        ratio = deviation / HP_LIMIT  # roughly in [-1, +1]
        buy_qsize = max(0, int(round(HP_QSIZE * (1.0 - ratio))))
        sell_qsize = max(0, int(round(HP_QSIZE * (1.0 + ratio))))
        buy_q = max(0, min(buy_qsize, HP_LIMIT - position - bv))
        sell_q = max(0, min(sell_qsize, HP_LIMIT + position - sv))
        if buy_q > 0:
            out.append(Order("HYDROGEL_PACK", bid_px, buy_q))
        if sell_q > 0:
            out.append(Order("HYDROGEL_PACK", ask_px, -sell_q))

    return out


# === Trader =================================================================
class Trader:
    def bid(self):
        return 0

    def run(self, state: TradingState):
        try:
            store = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            store = {}

        orders: dict[str, list[Order]] = {}

        hp = hp_orders(state, store)
        if hp:
            orders["HYDROGEL_PACK"] = hp

        trader_data = json.dumps(store)
        logger.flush(state, orders, 0, trader_data)
        return orders, 0, trader_data
