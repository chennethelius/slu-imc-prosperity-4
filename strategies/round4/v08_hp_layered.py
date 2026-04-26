"""Round 4 v08 — HP-only with LAYERED quotes for higher fill frequency.

User feedback: v07 holds inventory too long. To trade more often without
paying the spread, post quotes at MULTIPLE price levels (tiers) so we
catch a wider range of bot flow.

   Tier 1 (touch):   bid at best_bid+1,  ask at best_ask-1   — sized BASE
   Tier 2 (deeper):  bid at best_bid+1-1, ask at best_ask-1+1 — sized BASE/2

Both tiers capped at fair±1 so we never quote at-or-through fair=10000.

When a bot crosses the spread by 1 tick, our outer (deeper) tier catches
it. When they cross by 2, our inner tier fills. Either way we never pay
spread — both prices are profitable vs fair.

Plus: the same fair-value taking, position-reducing-at-fair, MR target
tilt, and inventory-aware sizing as v07.

Goal: push fills/tick higher → faster inventory turnover → tighter
position-MR loop → cleaner negative-skew curve.
"""

import json
from typing import Any

from datamodel import (
    Listing, Observation, Order, OrderDepth, ProsperityEncoder, Symbol, Trade, TradingState,
)


# === Logger =================================================================
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
HP_TIER1_QSIZE = 20      # tier 1 (touch+1)
HP_TIER2_QSIZE = 15      # tier 2 (touch+2 — deeper, sized smaller)
HP_MR_GAIN = 2
HP_SOFT_LIMIT = 60


def hp_orders(state, store):
    depth = state.order_depths.get("HYDROGEL_PACK")
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []

    position = state.position.get("HYDROGEL_PACK", 0)
    out = []
    bv = sv = 0

    # 1. Fair-value taking — only profitable crosses (ask < fair, bid > fair).
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

    # 2. Position-reducing at fair — flatten via @10000 orders that exist.
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

    # 3. Layered passive quotes with MR target + inventory skew.
    best_bid = max(depth.buy_orders); best_ask = min(depth.sell_orders)
    mid_now = (best_bid + best_ask) / 2.0
    target = max(-HP_SOFT_LIMIT, min(HP_SOFT_LIMIT,
                                     int(round(HP_MR_GAIN * (HP_FAIR - mid_now)))))
    pos_after_take = position + bv - sv
    deviation = pos_after_take - target
    ratio = deviation / HP_LIMIT
    buy_mult = max(0.0, 1.0 - ratio)
    sell_mult = max(0.0, 1.0 + ratio)

    # Tier 1 quotes — at the new touch (best_bid+1 / best_ask-1).
    bid_t1 = min(best_bid + 1, HP_FAIR - 1)
    ask_t1 = max(best_ask - 1, HP_FAIR + 1)
    if bid_t1 < ask_t1:
        buy_q = max(0, int(round(HP_TIER1_QSIZE * buy_mult)))
        sell_q = max(0, int(round(HP_TIER1_QSIZE * sell_mult)))
        buy_q = min(buy_q, HP_LIMIT - position - bv)
        sell_q = min(sell_q, HP_LIMIT + position - sv)
        if buy_q > 0:
            out.append(Order("HYDROGEL_PACK", bid_t1, buy_q)); bv += 0  # bv tracked only for taken
        if sell_q > 0:
            out.append(Order("HYDROGEL_PACK", ask_t1, -sell_q))

    # Tier 2 quotes — one tick deeper (best_bid+2 / best_ask-2). Smaller size.
    bid_t2 = min(best_bid + 2, HP_FAIR - 1)
    ask_t2 = max(best_ask - 2, HP_FAIR + 1)
    # Only post tier 2 if it's a different price than tier 1.
    if bid_t2 != bid_t1 and bid_t2 < ask_t2:
        buy_q2 = max(0, int(round(HP_TIER2_QSIZE * buy_mult)))
        # Don't double-count capacity used by tier 1
        used_bid = HP_TIER1_QSIZE  # pessimistic: assume tier 1 will fill
        buy_q2 = min(buy_q2, HP_LIMIT - position - bv - used_bid)
        if buy_q2 > 0:
            out.append(Order("HYDROGEL_PACK", bid_t2, buy_q2))
    if ask_t2 != ask_t1 and bid_t2 < ask_t2:
        sell_q2 = max(0, int(round(HP_TIER2_QSIZE * sell_mult)))
        used_ask = HP_TIER1_QSIZE
        sell_q2 = min(sell_q2, HP_LIMIT + position - sv - used_ask)
        if sell_q2 > 0:
            out.append(Order("HYDROGEL_PACK", ask_t2, -sell_q2))

    return out


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
