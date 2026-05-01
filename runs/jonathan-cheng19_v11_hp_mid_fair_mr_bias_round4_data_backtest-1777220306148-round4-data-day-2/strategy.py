"""Round 4 v11 — HP MM with two-tier fair: mid for quoting, 10000 for MR bias.

Core split (the user's spec):
  PRICE FAIR  = current mid-price        (used to set quote prices — always
                                           profitable vs current valuation)
  ANCHOR      = 10000 (static)           (MR target the position drifts toward)

Quote pricing (always profitable vs current mid):
  bid = best_bid + 1   capped at mid - 1
  ask = best_ask - 1   capped at mid + 1
  → every fill on our bid is at ≤ mid - 1 (profit ≥1 per lot vs mark)
  → every fill on our ask is at ≥ mid + 1 (profit ≥1 per lot vs mark)

Position-target bias (captures the MR-to-10000 alpha):
  target = HP_MR_GAIN * (10000 - mid)   clamped ±HP_SOFT_LIMIT
  When mid = 9990, target = +20 → bias buys → accumulate cheap inventory
  When mid = 10010, target = -20 → bias sells

Inventory-aware quote sizing pulls position toward target:
  deviation = position - target
  buy_qsize  = BASE * (1 - deviation/limit)
  sell_qsize = BASE * (1 + deviation/limit)

Plus the v10 "free profit" rules (never pay spread vs static):
  - Fair-value take: cross any ask < 10000, any bid > 10000
  - Position-reducing at 10000: take to flatten when |pos| > 0

Why this should improve over v10:
  v10 uses Kalman fair (tracks micro toward static 10000). When mid drifts
  fast, Kalman lags → quotes pricedrelative to a slightly stale fair → some
  quotes are at "stale-profitable" prices that may be unprofitable vs the
  current mid (which is what gets marked).
  v11 uses mid directly for quoting → every quote is profitable vs current
  mark → MTM dips during drift are smaller → lower drawdown.
  The MR alpha is preserved in the position-target bias, just expressed
  through quote size asymmetry instead of the Kalman fair offset.
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


# === Constants ==============================================================
HP_LIMIT = 200
HP_ANCHOR = 10000           # long-term mean (static)
HP_QSIZE = 30
HP_MR_GAIN = 2              # lots of position-bias per tick of (anchor - mid)
HP_SOFT_LIMIT = 60          # max desired position bias (30% of limit)


def hp_orders(d, pos, td):
    if not d.buy_orders or not d.sell_orders:
        return []
    bb = max(d.buy_orders)
    ba = min(d.sell_orders)
    mid = (bb + ba) / 2.0
    mid_int = round(mid)

    out = []
    bv = sv = 0

    # 1. Fair-value taking vs ANCHOR (free PnL — only crosses below 10000 buy / above 10000 sell)
    for ask in sorted(d.sell_orders):
        if ask >= HP_ANCHOR:
            break
        avail = -d.sell_orders[ask]
        qty = min(avail, HP_LIMIT - pos - bv)
        if qty <= 0: break
        out.append(Order("HYDROGEL_PACK", ask, qty)); bv += qty
    for bid in sorted(d.buy_orders, reverse=True):
        if bid <= HP_ANCHOR:
            break
        avail = d.buy_orders[bid]
        qty = min(avail, HP_LIMIT + pos - sv)
        if qty <= 0: break
        out.append(Order("HYDROGEL_PACK", bid, -qty)); sv += qty

    # 2. Position-reducing at ANCHOR (10000) when carrying inventory
    pos_after = pos + bv - sv
    if pos_after > 0 and HP_ANCHOR in d.buy_orders:
        avail = d.buy_orders[HP_ANCHOR]
        qty = min(avail, pos_after, HP_LIMIT + pos - sv)
        if qty > 0:
            out.append(Order("HYDROGEL_PACK", HP_ANCHOR, -qty)); sv += qty
    if pos_after < 0 and HP_ANCHOR in d.sell_orders:
        avail = -d.sell_orders[HP_ANCHOR]
        qty = min(avail, -pos_after, HP_LIMIT - pos - bv)
        if qty > 0:
            out.append(Order("HYDROGEL_PACK", HP_ANCHOR, qty)); bv += qty

    # 3. MR position target — pulls inventory toward profitable side as mid drifts.
    target = max(-HP_SOFT_LIMIT, min(HP_SOFT_LIMIT,
                                     int(round(HP_MR_GAIN * (HP_ANCHOR - mid)))))

    # 4. Passive quotes priced AT MID ± 1 (always +1 per lot vs current mark).
    bid_px = min(bb + 1, mid_int - 1)
    ask_px = max(ba - 1, mid_int + 1)
    if bid_px < ask_px:
        pos_after_take = pos + bv - sv
        deviation = pos_after_take - target
        ratio = deviation / HP_LIMIT
        buy_qsize = max(0, int(round(HP_QSIZE * (1.0 - ratio))))
        sell_qsize = max(0, int(round(HP_QSIZE * (1.0 + ratio))))
        buy_q = max(0, min(buy_qsize, HP_LIMIT - pos - bv))
        sell_q = max(0, min(sell_qsize, HP_LIMIT + pos - sv))
        if buy_q > 0:
            out.append(Order("HYDROGEL_PACK", bid_px, buy_q))
        if sell_q > 0:
            out.append(Order("HYDROGEL_PACK", ask_px, -sell_q))

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
            ors = hp_orders(depth, state.position.get("HYDROGEL_PACK", 0), td)
            if ors:
                orders["HYDROGEL_PACK"] = ors
        trader_data = json.dumps(td)
        logger.flush(state, orders, 0, trader_data)
        return orders, 0, trader_data
