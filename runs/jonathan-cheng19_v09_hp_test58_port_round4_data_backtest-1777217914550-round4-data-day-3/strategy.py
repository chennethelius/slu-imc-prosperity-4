"""Round 4 v09 — HP using Test_58's full OSMIUM-style template.

Test_58 was a Round-2 winner for ASH_COATED_OSMIUM (same product profile
as HYDROGEL_PACK: fair ≈ 10000, low volatility, static reversion). Porting
the entire OSM pipeline to HP, scaling position-related constants 80→200.

Adopted techniques from Test_58:

  1. Kalman fair = static + K_SS*(micro - fair). Tracks the micro-price
     adaptively but pulled toward static 10000.

  2. Asymmetric take bounds:
        take_buy  = max(static, fair)   — willing to BUY up to here
        take_sell = min(static, fair)   — willing to SELL down to here
     We only TAKE asks when ask <= take_buy - tw_ask  (i.e. ask is below
     the higher of static/fair, plus we want a margin).

  3. Inventory-aware take width:
        skew = round(pos / SKEW_UNIT)
        tw_ask = max(0, TAKE_WIDTH + skew)   — harder to add longs when long
        tw_bid = max(0, TAKE_WIDTH - skew)   — easier to add shorts when long
     Self-balancing inventory pressure on the take side.

  4. Adaptive CLEAR width:
        cw = CLEAR_WIDTH - (1 if |pos| >= CLEAR_TIGHT_POS else 0)
     Tighter clear when carrying meaningful inventory — flatten faster.

  5. Book-sweep CLEAR at fair±cw:
        if pos > 0: sell at f_ask=round(fair+cw) up to ALL bids >= f_ask
        if pos < 0: buy  at f_bid=round(fair-cw) up to ALL asks <= f_bid
     Hammers the resting book in the unwind direction at a profitable price.

  6. Skewed make edges:
        bid_edge = max(1, MAKE_EDGE + skew)
        ask_edge = max(1, MAKE_EDGE - skew)
     When long, post bid further from fair (less likely to fill — don't add)
     and ask closer to fair (more likely to fill — unwind).

  7. Queue-jumping gate:
        if outer ask <= fair + ask_edge and pos <= VOLUME_LIMIT:
            push quote 1 tick further inside spread
     Only quote-jump when not over-loaded.

VFE/VEV not included — HP-only isolated per the user's earlier request.

Constants scaled from Test_58 (limit 80 → 200): CLEAR_TIGHT_POS 50→125,
VOLUME_LIMIT 30→75. K_SS, TAKE_WIDTH, CLEAR_WIDTH, MAKE_EDGE, SKEW_UNIT
unchanged (these are price-domain not position-domain).
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


# === Constants (Test_58 OSMIUM ported to HP, position-scaled 80→200) ========
HP_LIMIT = 200
HP_FAIR_STATIC = 10000
HP_K_SS = 0.1353
HP_TAKE_WIDTH = 3
HP_CLEAR_WIDTH = 2
HP_CLEAR_TIGHT_POS = 125    # scaled from Test_58's 50 (50/80 → 125/200)
HP_VOLUME_LIMIT = 75        # scaled from Test_58's 30 (30/80 → 75/200)
HP_MAKE_EDGE = 2
HP_SKEW_UNIT = 24


def _kalman_fair(depth, td):
    if not depth.buy_orders or not depth.sell_orders:
        return td.get("_hp_f", HP_FAIR_STATIC)
    bb = max(depth.buy_orders)
    ba = min(depth.sell_orders)
    bv = depth.buy_orders[bb]
    av = -depth.sell_orders[ba]
    tot = bv + av
    micro = (bb * av + ba * bv) / tot if tot > 0 else (bb + ba) / 2.0
    f = td.get("_hp_f", micro)
    f += HP_K_SS * (micro - f)
    td["_hp_f"] = f
    return f


def hp_orders(d, pos, td):
    if not d.buy_orders or not d.sell_orders:
        return []
    fair = _kalman_fair(d, td)
    take_buy = max(HP_FAIR_STATIC, fair)
    take_sell = min(HP_FAIR_STATIC, fair)

    lim = HP_LIMIT
    cw = HP_CLEAR_WIDTH - (1 if abs(pos) >= HP_CLEAR_TIGHT_POS else 0)
    orders = []
    bv = sv = 0

    skew = round(pos / HP_SKEW_UNIT)
    tw_ask = max(0, HP_TAKE_WIDTH + skew)
    tw_bid = max(0, HP_TAKE_WIDTH - skew)

    # 1. Asymmetric inventory-aware take
    ba = min(d.sell_orders)
    if ba <= take_buy - tw_ask:
        q = min(-d.sell_orders[ba], lim - pos - bv)
        if q > 0:
            orders.append(Order("HYDROGEL_PACK", ba, q)); bv += q
    bb = max(d.buy_orders)
    if bb >= take_sell + tw_bid:
        q = min(d.buy_orders[bb], lim + pos - sv)
        if q > 0:
            orders.append(Order("HYDROGEL_PACK", bb, -q)); sv += q

    # 2. Adaptive clear at fair ± cw (book sweep)
    pos_after = pos + bv - sv
    f_bid = int(round(fair - cw))
    f_ask = int(round(fair + cw))
    if pos_after > 0:
        cq = min(pos_after, sum(v for p, v in d.buy_orders.items() if p >= f_ask))
        sent = min(lim + pos - sv, cq)
        if sent > 0:
            orders.append(Order("HYDROGEL_PACK", f_ask, -sent)); sv += sent
    elif pos_after < 0:
        cq = min(-pos_after, sum(-v for p, v in d.sell_orders.items() if p <= f_bid))
        sent = min(lim - pos - bv, cq)
        if sent > 0:
            orders.append(Order("HYDROGEL_PACK", f_bid, sent)); bv += sent

    # 3. Skewed make + queue-jump-when-not-overloaded
    bid_edge = max(1, HP_MAKE_EDGE + skew)
    ask_edge = max(1, HP_MAKE_EDGE - skew)
    ask_gate = fair + ask_edge - 1
    bid_gate = fair - bid_edge + 1
    baaf = min((p for p in d.sell_orders if p > ask_gate), default=None)
    bbbf = max((p for p in d.buy_orders if p < bid_gate), default=None)
    if baaf is not None and bbbf is not None:
        if baaf <= fair + ask_edge and pos <= HP_VOLUME_LIMIT:
            baaf = int(round(fair + ask_edge + 1))
        if bbbf >= fair - bid_edge and pos >= -HP_VOLUME_LIMIT:
            bbbf = int(round(fair - bid_edge - 1))
        buy_q = lim - pos - bv
        if buy_q > 0:
            orders.append(Order("HYDROGEL_PACK", int(bbbf + 1), buy_q))
        sell_q = lim + pos - sv
        if sell_q > 0:
            orders.append(Order("HYDROGEL_PACK", int(baaf - 1), -sell_q))
    return orders


# === Trader =================================================================
class Trader:
    def bid(self):
        return 0

    def run(self, state: TradingState):
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}
        orders: dict[str, list[Order]] = {}
        depth = state.order_depths.get("HYDROGEL_PACK")
        if depth:
            ors = hp_orders(depth, state.position.get("HYDROGEL_PACK", 0), td)
            if ors:
                orders["HYDROGEL_PACK"] = ors
        trader_data = json.dumps(td)
        logger.flush(state, orders, 0, trader_data)
        return orders, 0, trader_data
