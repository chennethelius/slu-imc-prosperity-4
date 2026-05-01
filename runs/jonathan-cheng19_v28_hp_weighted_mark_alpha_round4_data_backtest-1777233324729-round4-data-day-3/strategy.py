"""Round 4 v28 — v27 + weighted per-counterparty classification.

Comprehensive R3+R4 forward-return analysis (5000ms / 50-tick horizon)
classified each Mark's HP behavior:

   Mark    role             fwd_b    fwd_s   weight     evidence
   ------------------------------------------------------------------
   Mark 14 informed-follow  +0.61   −1.14    +1.5       n=1003 trades
   Mark 38 noise-fade       −1.12   +0.33    −1.0       n=1022 trades
   Mark 22 noise-fade       −12.45  +0.12    −0.5       n=19 (small)
   others  not active on HP                  0          —

VFE → HP cross-product correlation is ~0 at every lag (all |corr| <0.022),
so VFE flow doesn't help HP. The exploitable alpha is purely:
   1. Within-HP mean reversion (z-score, EMA confluence)
   2. HP counterparty classification (Mark 14, 38, 22)

v28 differential weighting: Mark 14's signal is cleaner (consistent
forward returns both directions, large sample) — give it 1.5× weight.
Mark 38's signal is weaker (only one-side fade) — 1.0× weight.
Mark 22's sample is small — 0.5× weight (low confidence).

Implementation:
   net_inf = Σ over recent trades:
              + w(buyer) * qty   if buyer is classified
              - w(seller) * qty  if seller is classified
   where w > 0 means follow (treat their direction as bullish)
   and the negative weights for fade-Marks make them act as inverse signals.

Same conviction blend, dynamic-cap, MM leg as v27 — only counterparty
weighting changes.
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
HP_FAIR = 10002
HP_STDEV_INIT = 33.0
HP_VAR_ALPHA = 0.005

HP_QSIZE = 35
HP_FLAT_PULL = 1.0
HP_MR_THRESH = 4
HP_MR_BOOST = 1.5

HP_Z_MIN = 0.7
HP_Z_MAX = 2.0

HP_EMA_FAST_ALPHA = 0.30
HP_EMA_SLOW_ALPHA = 0.05
HP_EMA_VSLOW_ALPHA = 0.02
HP_EMA_FULL = 1.5

HP_INFORMED_LOOKBACK = 8
HP_INFORMED_FULL = 30

HP_W_Z = 0.50
HP_W_EMA = 0.30
HP_W_INF = 0.20

HP_TAKE_MAX = 80
HP_TAKE_OFFSET = 4
HP_BASE_CAP_PCT = 0.50
HP_FULL_CAP_PCT = 1.00
HP_HARD_CAP_PCT = 0.95

# v28 NEW: weighted per-counterparty alpha (R3+R4 5000ms fwd-return analysis)
# Positive weight = follow (their direction predicts mid moves in their favor)
# Negative weight = fade (their direction predicts mid moves AGAINST them)
HP_MARK_WEIGHTS = {
    "Mark 14": +1.5,   # informed follow (fwd_b +0.61, fwd_s -1.14, n=1003)
    "Mark 38": -1.0,   # noise fade (fwd_b -1.12, fwd_s +0.33, n=1022)
    "Mark 22": -0.5,   # small-sample fade (fwd_b -12.45, n=19, low confidence)
}


def _conviction(mid, market_trades, td):
    dev = mid - HP_FAIR
    var = td.get("_var", HP_STDEV_INIT * HP_STDEV_INIT)
    var = (1.0 - HP_VAR_ALPHA) * var + HP_VAR_ALPHA * (dev * dev)
    td["_var"] = var
    stdev = max(5.0, var ** 0.5)
    z = dev / stdev

    abs_z = abs(z)
    z_str = 0.0 if abs_z < HP_Z_MIN else min(1.0, (abs_z - HP_Z_MIN) / (HP_Z_MAX - HP_Z_MIN))
    direction = +1 if dev < 0 else -1

    ema_f = td.get("_hp_ef", mid)
    ema_s = td.get("_hp_es", mid)
    ema_vs = td.get("_hp_evs", mid)
    ema_f = HP_EMA_FAST_ALPHA * mid + (1.0 - HP_EMA_FAST_ALPHA) * ema_f
    ema_s = HP_EMA_SLOW_ALPHA * mid + (1.0 - HP_EMA_SLOW_ALPHA) * ema_s
    ema_vs = HP_EMA_VSLOW_ALPHA * mid + (1.0 - HP_EMA_VSLOW_ALPHA) * ema_vs
    td["_hp_ef"] = ema_f
    td["_hp_es"] = ema_s
    td["_hp_evs"] = ema_vs

    short = ema_f - ema_s
    medium = ema_s - ema_vs
    short_sign = 1 if short > 0 else (-1 if short < 0 else 0)
    medium_sign = 1 if medium > 0 else (-1 if medium < 0 else 0)
    if short_sign != 0 and short_sign == medium_sign and short_sign == direction:
        ema_str = min(1.0, abs(short) / HP_EMA_FULL)
    else:
        ema_str = 0.0

    # === v28: weighted per-Mark flow ===
    net_inf = 0.0
    if market_trades:
        for t in market_trades[-HP_INFORMED_LOOKBACK:]:
            buyer = (t.buyer or "")
            seller = (t.seller or "")
            qty = int(t.quantity)
            w_buyer = HP_MARK_WEIGHTS.get(buyer, 0.0)
            w_seller = HP_MARK_WEIGHTS.get(seller, 0.0)
            # Follow Mark (w>0): their buy = bullish (price goes up next).
            # Fade Mark (w<0): their buy = bearish (price drops next).
            net_inf += w_buyer * qty
            net_inf -= w_seller * qty
    inf_sign = 1 if net_inf > 0 else (-1 if net_inf < 0 else 0)
    if inf_sign != 0 and inf_sign == direction:
        inf_str = min(1.0, abs(net_inf) / HP_INFORMED_FULL)
    else:
        inf_str = 0.0

    conviction = HP_W_Z * z_str + HP_W_EMA * ema_str + HP_W_INF * inf_str
    if z_str == 0.0:
        return 0, 0.0
    return direction, conviction


def hp_orders(d, pos, td, market_trades):
    if not d.buy_orders or not d.sell_orders:
        return []
    bb = max(d.buy_orders)
    ba = min(d.sell_orders)
    mid = (bb + ba) / 2.0

    out = []
    bv = sv = 0

    direction, conviction = _conviction(mid, market_trades, td)

    cap_lots = HP_HARD_CAP_PCT * HP_LIMIT
    if pos > cap_lots:
        for bid in sorted(d.buy_orders, reverse=True):
            if bid < HP_FAIR - 2:
                break
            avail = d.buy_orders[bid]
            qty = min(avail, pos, HP_LIMIT + pos - sv)
            if qty <= 0:
                break
            out.append(Order("HYDROGEL_PACK", bid, -qty)); sv += qty
            if pos + bv - sv <= cap_lots * 0.5:
                break
    elif pos < -cap_lots:
        for ask in sorted(d.sell_orders):
            if ask > HP_FAIR + 2:
                break
            avail = -d.sell_orders[ask]
            qty = min(avail, -pos, HP_LIMIT - pos - bv)
            if qty <= 0:
                break
            out.append(Order("HYDROGEL_PACK", ask, qty)); bv += qty
            if pos + bv - sv >= -cap_lots * 0.5:
                break

    pos_after = pos + bv - sv
    dyn_cap_pct = HP_BASE_CAP_PCT + (HP_FULL_CAP_PCT - HP_BASE_CAP_PCT) * conviction
    soft_cap = dyn_cap_pct * HP_LIMIT

    if conviction > 0:
        target_qty = int(round(HP_TAKE_MAX * conviction))
        if direction > 0 and pos_after < soft_cap:
            max_pay = HP_FAIR + HP_TAKE_OFFSET
            remaining = target_qty
            for ask in sorted(d.sell_orders):
                if ask > max_pay or remaining <= 0:
                    break
                avail = -d.sell_orders[ask]
                room = HP_LIMIT - pos - bv
                cap_room = int(soft_cap - pos_after)
                qty = min(avail, room, cap_room, remaining)
                if qty <= 0:
                    break
                out.append(Order("HYDROGEL_PACK", ask, qty)); bv += qty
                remaining -= qty
                pos_after = pos + bv - sv
        elif direction < 0 and pos_after > -soft_cap:
            min_recv = HP_FAIR - HP_TAKE_OFFSET
            remaining = target_qty
            for bid in sorted(d.buy_orders, reverse=True):
                if bid < min_recv or remaining <= 0:
                    break
                avail = d.buy_orders[bid]
                room = HP_LIMIT + pos - sv
                cap_room = int(soft_cap + pos_after)
                qty = min(avail, room, cap_room, remaining)
                if qty <= 0:
                    break
                out.append(Order("HYDROGEL_PACK", bid, -qty)); sv += qty
                remaining -= qty
                pos_after = pos + bv - sv

    pos_after = pos + bv - sv

    mr_dir = 0
    if mid < HP_FAIR - HP_MR_THRESH:
        mr_dir = +1
    elif mid > HP_FAIR + HP_MR_THRESH:
        mr_dir = -1

    bid_px = min(bb + 1, HP_FAIR - 1)
    ask_px = max(ba - 1, HP_FAIR + 1)

    ratio = pos_after / HP_LIMIT
    buy_mult = max(0.0, 1.0 - HP_FLAT_PULL * ratio)
    sell_mult = max(0.0, 1.0 + HP_FLAT_PULL * ratio)
    if mr_dir > 0:
        buy_mult *= HP_MR_BOOST
    elif mr_dir < 0:
        sell_mult *= HP_MR_BOOST

    buy_q = max(0, int(round(HP_QSIZE * buy_mult)))
    sell_q = max(0, int(round(HP_QSIZE * sell_mult)))
    buy_q = max(0, min(buy_q, HP_LIMIT - pos - bv))
    sell_q = max(0, min(sell_q, HP_LIMIT + pos - sv))

    if bid_px < ask_px:
        if buy_q > 0:
            out.append(Order("HYDROGEL_PACK", int(bid_px), buy_q))
        if sell_q > 0:
            out.append(Order("HYDROGEL_PACK", int(ask_px), -sell_q))

    return out


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
            mts = state.market_trades.get("HYDROGEL_PACK", [])
            ors = hp_orders(depth, state.position.get("HYDROGEL_PACK", 0), td, mts)
            if ors:
                orders["HYDROGEL_PACK"] = ors
        trader_data = json.dumps(td)
        logger.flush(state, orders, 0, trader_data)
        return orders, 0, trader_data
