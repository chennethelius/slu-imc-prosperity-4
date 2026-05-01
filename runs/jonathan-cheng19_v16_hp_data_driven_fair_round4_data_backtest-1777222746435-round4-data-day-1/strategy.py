"""Round 4 v16 — v10 with data-driven static fair = 10002.

Single-line change from v10. Parameter sweep over HP_FAIR_STATIC ∈
{9994..10006} on the round-4 backtest:

    fair=9994   pnl= 66,800   sharpe= 3.60   DD=21,163
    fair=9996   pnl= 80,438   sharpe= 5.46   DD=21,165
    fair=9998   pnl= 86,083   sharpe= 7.74   DD=21,231
    fair=9999   pnl= 90,621   sharpe= 8.99   DD=21,238
    fair=10000  pnl= 93,366   sharpe=10.51   DD=21,238   ← v10 baseline
    fair=10001  pnl= 95,769   sharpe=10.45   DD=21,252
    fair=10002  pnl= 98,262   sharpe=11.31   DD=21,252   ← v16
    fair=10004  pnl=105,045   sharpe= 6.66   DD=21,401
    fair=10006  pnl=108,422   sharpe= 4.61   DD=22,071

10002 is the sweet spot — PnL up +5%, Sharpe up +0.80, drawdown unchanged.
Higher static (10004/10006) gives more PnL but Sharpe collapses (volatile
unrealized swings while accumulating long inventory).

Why 10002 instead of the data median (9996)?
The static is used in take_buy = max(STATIC, fair_kalman) and take_sell =
min(STATIC, fair_kalman). Setting it slightly above the long-run mean biases
our take edge toward buying dips (asks below STATIC become takeable even
when Kalman fair has risen), which compounds with the Kalman's slight lag
during reversion to capture the negative-skew alpha (long-when-cheap).

The drawdown floor is set by the structural make/take logic, not the static
value (DD ≈ 21k across all candidates). So we're harvesting more alpha
without taking on more inventory risk.
"""

import json
from typing import Any

from datamodel import (
    Listing, Observation, Order, OrderDepth, ProsperityEncoder, Symbol, Trade, TradingState,
)


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


# === Constants ==============================================================
HP_LIMIT = 200
HP_FAIR_STATIC = 10002       # was 10000 in v10 — data-driven optimum
HP_K_SS = 0.1353
HP_TAKE_WIDTH = 3
HP_CLEAR_WIDTH = 1
HP_CLEAR_TIGHT_POS = 40
HP_VOLUME_LIMIT = 30
HP_MAKE_EDGE = 2
HP_SKEW_UNIT = 12


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
    cw = max(1, HP_CLEAR_WIDTH - (1 if abs(pos) >= HP_CLEAR_TIGHT_POS else 0))
    orders = []
    bv = sv = 0

    skew = round(pos / HP_SKEW_UNIT)
    tw_ask = max(0, HP_TAKE_WIDTH + skew)
    tw_bid = max(0, HP_TAKE_WIDTH - skew)

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
