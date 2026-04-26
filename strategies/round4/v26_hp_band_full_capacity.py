"""Round 4 v26 — band-based MR with full ±200 inventory capacity.

User directive:
  Use thresholds and bands to determine long/short behavior, utilize the
  ENTIRE ±200 inventory capacity to improve PnL.

v24 caps directional take at 50% (=100 lots). v26 lifts the ceiling: at
extreme deviations we commit the FULL inventory (±200 lots).

Discrete band-target architecture:

  z-score = (mid - fair) / stdev_ewma

  z < -2.5  → target = +200    (extreme cheap, full long)
  z < -1.7  → target = +150
  z < -1.0  → target =  +80
  z >  1.0  → target =  -80
  z >  1.7  → target = -150
  z >  2.5  → target = -200    (extreme rich, full short)
  inside    → if pos > 0 & mid ≥ fair: target = 0 (flatten on revert)
              elif pos < 0 & mid ≤ fair: target = 0
              else: target = pos (hold)

The walker reaches target by either crossing the spread or sweeping book
levels up to fair ± TAKE_OFFSET. Always-on MM at fair±1 captures small
incremental moves and pulls inventory back when target = pos.

Confluence gates (don't load full when momentum opposes):

  EMA short/medium confluence: if EMA fast/slow direction OPPOSES MR
  direction with |fast-slow| > THRESH → halve target. Don't fight strong
  momentum at full size.

  Mark-14 informed flow alignment: if Mark 14 (informed) is selling while
  z says buy → halve target. Real informed flow against us means our z
  reading might be premature.

This is more aggressive than v24 in two ways:
  1. Full ±200 cap (vs 100)
  2. Discrete bands (commits to full target immediately, not graduated)

Risk: if mean-reversion fails to materialize, larger positions = larger
MTM swings. The momentum/informed gates protect against the worst cases.
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

# Band thresholds (z-score units)
HP_Z_BAND_1 = 1.0
HP_Z_BAND_2 = 1.7
HP_Z_BAND_3 = 2.5

# Position targets per band (full ±200 at extreme)
HP_TGT_BAND_1 = 80
HP_TGT_BAND_2 = 150
HP_TGT_BAND_3 = 200

# Take-walker constraints
HP_TAKE_OFFSET = 5            # max ticks beyond fair we'll cross

# Confluence gates
HP_EMA_FAST_ALPHA = 0.30
HP_EMA_SLOW_ALPHA = 0.05
HP_EMA_OPPOSE_HALVE = 0.6     # |EMA fast-slow| > this opposing z → halve target

# Per-Mark counterparty alpha (from R3+R4 fwd-return analysis, HP)
HP_MARK_FOLLOW = {"Mark 14"}
HP_MARK_FADE = {"Mark 38"}
HP_INFORMED_LOOKBACK = 8
HP_INFORMED_OPPOSE_THRESH = 15  # net informed flow >this opposing → halve target

# Always-on MM
HP_MM_QSIZE = 30
HP_MM_FLAT_PULL = 1.0


def _z_and_filters(mid, market_trades, td):
    # Z-score
    dev = mid - HP_FAIR
    var = td.get("_var", HP_STDEV_INIT * HP_STDEV_INIT)
    var = (1.0 - HP_VAR_ALPHA) * var + HP_VAR_ALPHA * (dev * dev)
    td["_var"] = var
    stdev = max(5.0, var ** 0.5)
    z = dev / stdev

    # EMA momentum
    ema_f = td.get("_hp_ef", mid)
    ema_s = td.get("_hp_es", mid)
    ema_f = HP_EMA_FAST_ALPHA * mid + (1.0 - HP_EMA_FAST_ALPHA) * ema_f
    ema_s = HP_EMA_SLOW_ALPHA * mid + (1.0 - HP_EMA_SLOW_ALPHA) * ema_s
    td["_hp_ef"] = ema_f
    td["_hp_es"] = ema_s
    ema_diff = ema_f - ema_s

    # Per-Mark classified flow
    inf_flow = 0
    if market_trades:
        for t in market_trades[-HP_INFORMED_LOOKBACK:]:
            buyer = (t.buyer or "")
            seller = (t.seller or "")
            qty = int(t.quantity)
            if buyer in HP_MARK_FOLLOW:
                inf_flow += qty
            elif buyer in HP_MARK_FADE:
                inf_flow -= qty
            if seller in HP_MARK_FOLLOW:
                inf_flow -= qty
            elif seller in HP_MARK_FADE:
                inf_flow += qty

    return z, ema_diff, inf_flow


def _band_target(z):
    if z <= -HP_Z_BAND_3:
        return +HP_TGT_BAND_3
    if z <= -HP_Z_BAND_2:
        return +HP_TGT_BAND_2
    if z <= -HP_Z_BAND_1:
        return +HP_TGT_BAND_1
    if z >= HP_Z_BAND_3:
        return -HP_TGT_BAND_3
    if z >= HP_Z_BAND_2:
        return -HP_TGT_BAND_2
    if z >= HP_Z_BAND_1:
        return -HP_TGT_BAND_1
    return None  # in-band — caller handles (flatten or hold)


def hp_orders(d, pos, td, market_trades):
    if not d.buy_orders or not d.sell_orders:
        return []
    bb = max(d.buy_orders)
    ba = min(d.sell_orders)
    mid = (bb + ba) / 2.0

    z, ema_diff, inf_flow = _z_and_filters(mid, market_trades, td)

    band_target = _band_target(z)
    if band_target is not None:
        # Apply confluence gates: halve target if momentum or informed flow OPPOSES
        target_sign = 1 if band_target > 0 else -1
        # EMA opposes when sign(ema_diff) opposite to target_sign and |ema_diff| > THRESH
        ema_opposes = (target_sign > 0 and ema_diff < -HP_EMA_OPPOSE_HALVE) or \
                      (target_sign < 0 and ema_diff > HP_EMA_OPPOSE_HALVE)
        # Informed flow opposes
        inf_opposes = (target_sign > 0 and inf_flow < -HP_INFORMED_OPPOSE_THRESH) or \
                      (target_sign < 0 and inf_flow > HP_INFORMED_OPPOSE_THRESH)
        if ema_opposes:
            band_target //= 2
        if inf_opposes:
            band_target //= 2
        target = band_target
    else:
        # In-band: flatten when reverted, else hold
        if pos > 0 and mid >= HP_FAIR:
            target = 0
        elif pos < 0 and mid <= HP_FAIR:
            target = 0
        else:
            target = pos

    out = []
    bv = sv = 0
    qty_needed = target - pos

    # === Walk book to reach target ===
    if qty_needed > 0:
        max_pay = HP_FAIR + HP_TAKE_OFFSET
        remaining = qty_needed
        for ask in sorted(d.sell_orders):
            if ask > max_pay or remaining <= 0:
                break
            avail = -d.sell_orders[ask]
            qty = min(avail, remaining, HP_LIMIT - pos - bv)
            if qty <= 0:
                break
            out.append(Order("HYDROGEL_PACK", ask, qty)); bv += qty
            remaining -= qty
    elif qty_needed < 0:
        min_recv = HP_FAIR - HP_TAKE_OFFSET
        remaining = -qty_needed
        for bid in sorted(d.buy_orders, reverse=True):
            if bid < min_recv or remaining <= 0:
                break
            avail = d.buy_orders[bid]
            qty = min(avail, remaining, HP_LIMIT + pos - sv)
            if qty <= 0:
                break
            out.append(Order("HYDROGEL_PACK", bid, -qty)); sv += qty
            remaining -= qty

    # === Always-on MM at fair±1 (asymmetric, pulls toward target) ============
    pos_after = pos + bv - sv
    bid_px = min(bb + 1, HP_FAIR - 1)
    ask_px = max(ba - 1, HP_FAIR + 1)

    # Asymmetric: bias quote sizes toward TARGET (not zero)
    # Distance from target → larger quote on the closing side
    diff_from_target = pos_after - target
    norm = diff_from_target / HP_LIMIT  # ∈ [-2, +2]
    # If we're above target → sell more; below target → buy more
    buy_mult = max(0.0, 1.0 - HP_MM_FLAT_PULL * norm)
    sell_mult = max(0.0, 1.0 + HP_MM_FLAT_PULL * norm)

    buy_q = max(0, int(round(HP_MM_QSIZE * buy_mult)))
    sell_q = max(0, int(round(HP_MM_QSIZE * sell_mult)))
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
