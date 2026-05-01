"""Round 4 v18 — separated MM (flat) + signal-gated taking.

User architecture:
  Make leg → net inventory ≈ 0, only skew sizes when capturing MR alpha
  Take leg → only fires on directional signal (informed counterparty OR EMA)

This split forces the maker book to mean-revert to flat inventory by default.
Taking is reserved as a directional-conviction tool, not free-PnL noise capture.

Components:

  1. Static fair = 10002 (data-driven from R3+R4 sweep, see v16).

  2. MAKE leg — symmetric quotes when fair, MR-skewed when signal:
       bid_px = bb + 1   (price-improve to top of bid)
       ask_px = ba - 1   (price-improve to top of ask)
       both clamped: bid <= fair-1, ask >= fair+1 (never cross MTM fair)
     Quote SIZES:
       no-MR-signal (mid within ±MR_THRESH of fair):
            buy_q = sell_q = QSIZE * pull_to_zero(pos)
            pulls inventory toward zero with mild asymmetry
       MR-signal (mid drifts away from fair by > MR_THRESH):
            buy_q boosted when mid < fair-thresh (cheap, accumulate long)
            sell_q boosted when mid > fair+thresh (rich, accumulate short)
     Net effect: book is balanced unless price wanders → then MM leans into MR.

  3. TAKE leg — gated by signals:
       Signal A (EMA momentum):  ema_fast - ema_slow > TREND_THRESH
            → directional momentum detected, take in the direction
       Signal B (informed traders): net (buys - sells) over last K market
            trades exceeds INFORMED_THRESH. Mark-* counterparties are
            tagged informed by the round-4 buyer/seller log.
       When EITHER signal fires (and aligns), aggressively cross to build
       inventory in the signal direction up to a soft cap.
       NO SIGNAL → NO TAKING (the MM leg handles everything else).

  4. Hard inventory cap at 90% of limit — emergency unwind via crossing.

Why this should help:
  v16 (and v10) take liberally any time book is wrong vs static fair, so
  inventory accumulates from passive AND active flows that may not be aligned.
  v18 forces taking to be conviction-driven, leaving routine market noise
  to the MM leg which auto-flattens. Should yield similar or better PnL with
  smaller drawdown and faster inventory turnover.
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
HP_FAIR = 10002          # data-driven static fair (v16 sweep optimum)

# MAKE leg
HP_QSIZE = 35            # base symmetric size
HP_FLAT_PULL = 1.0       # strength of pull-toward-zero in size asymmetry
HP_MR_THRESH = 4         # |mid - fair| > 4 ticks → MR signal active
HP_MR_BOOST = 1.5        # multiplier on signal-side quote when MR active

# TAKE leg
HP_EMA_FAST_ALPHA = 0.30
HP_EMA_SLOW_ALPHA = 0.05
HP_EMA_TREND_THRESH = 0.4   # |ema_fast - ema_slow| > 0.4 → trend signal
HP_INFORMED_LOOKBACK = 8     # last K market trades to scan for informed flow
HP_INFORMED_THRESH = 12      # net informed buy-vol - sell-vol threshold
HP_TAKE_QSIZE = 60          # how aggressive to cross when signal fires
HP_TAKE_SOFT_CAP_PCT = 0.50  # don't push directional inventory past 50%

# Hard cap
HP_HARD_CAP_PCT = 0.90       # emergency unwind past 90%


def _ema_signal(mid, td):
    ema_f = td.get("_hp_ef", mid)
    ema_s = td.get("_hp_es", mid)
    ema_f = HP_EMA_FAST_ALPHA * mid + (1.0 - HP_EMA_FAST_ALPHA) * ema_f
    ema_s = HP_EMA_SLOW_ALPHA * mid + (1.0 - HP_EMA_SLOW_ALPHA) * ema_s
    td["_hp_ef"] = ema_f
    td["_hp_es"] = ema_s
    diff = ema_f - ema_s
    if diff > HP_EMA_TREND_THRESH:
        return +1, diff
    if diff < -HP_EMA_TREND_THRESH:
        return -1, diff
    return 0, diff


def _informed_signal(market_trades):
    """Net direction from named-Mark counterparties in last K trades.
    Returns +1 if informed buyers dominated, -1 if sellers, 0 otherwise."""
    if not market_trades:
        return 0
    recent = market_trades[-HP_INFORMED_LOOKBACK:]
    net = 0
    for t in recent:
        buyer = (t.buyer or "")
        seller = (t.seller or "")
        if buyer.startswith("Mark"):
            net += int(t.quantity)
        if seller.startswith("Mark"):
            net -= int(t.quantity)
    if net > HP_INFORMED_THRESH:
        return +1
    if net < -HP_INFORMED_THRESH:
        return -1
    return 0


def hp_orders(d, pos, td, market_trades):
    if not d.buy_orders or not d.sell_orders:
        return []
    bb = max(d.buy_orders)
    ba = min(d.sell_orders)
    mid = (bb + ba) / 2.0
    mid_int = int(round(mid))

    out = []
    bv = sv = 0

    # === Signal generation ==================================================
    ema_dir, ema_diff = _ema_signal(mid, td)
    inf_dir = _informed_signal(market_trades)
    # Take signal fires only when EMA and informed AGREE (or one is strong
    # and the other is neutral). Conflict → no take.
    if ema_dir != 0 and inf_dir != 0 and ema_dir != inf_dir:
        take_dir = 0
    else:
        take_dir = ema_dir or inf_dir

    # === Hard cap (always-on unwind past 90%) ===============================
    cap_lots = HP_HARD_CAP_PCT * HP_LIMIT
    if pos > cap_lots:
        for bid in sorted(d.buy_orders, reverse=True):
            if bid < HP_FAIR - 2:
                break
            avail = d.buy_orders[bid]
            qty = min(avail, pos - 0, HP_LIMIT + pos - sv)
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
            qty = min(avail, -pos - 0, HP_LIMIT - pos - bv)
            if qty <= 0:
                break
            out.append(Order("HYDROGEL_PACK", ask, qty)); bv += qty
            if pos + bv - sv >= -cap_lots * 0.5:
                break

    # === TAKE leg — only when signal fires and aligns =======================
    pos_after = pos + bv - sv
    soft_cap = HP_TAKE_SOFT_CAP_PCT * HP_LIMIT
    if take_dir > 0 and pos_after < soft_cap:
        # Bullish: lift asks at-or-below fair (don't pay above fair)
        budget = HP_TAKE_QSIZE
        for ask in sorted(d.sell_orders):
            if ask > HP_FAIR:
                break
            avail = -d.sell_orders[ask]
            room = HP_LIMIT - pos - bv
            cap_room = int(soft_cap - pos_after)
            qty = min(avail, room, cap_room, budget)
            if qty <= 0:
                break
            out.append(Order("HYDROGEL_PACK", ask, qty)); bv += qty
            budget -= qty
            pos_after = pos + bv - sv
            if budget <= 0 or pos_after >= soft_cap:
                break
    elif take_dir < 0 and pos_after > -soft_cap:
        # Bearish: hit bids at-or-above fair (don't sell below fair)
        budget = HP_TAKE_QSIZE
        for bid in sorted(d.buy_orders, reverse=True):
            if bid < HP_FAIR:
                break
            avail = d.buy_orders[bid]
            room = HP_LIMIT + pos - sv
            cap_room = int(soft_cap + pos_after)
            qty = min(avail, room, cap_room, budget)
            if qty <= 0:
                break
            out.append(Order("HYDROGEL_PACK", bid, -qty)); sv += qty
            budget -= qty
            pos_after = pos + bv - sv
            if budget <= 0 or pos_after <= -soft_cap:
                break

    # === MAKE leg — flat-by-default, MR-skewed when signal ==================
    pos_after = pos + bv - sv

    # MR signal: mid drifts > MR_THRESH away from fair → boost the buy-cheap or
    # sell-rich side.
    mr_dir = 0
    if mid < HP_FAIR - HP_MR_THRESH:
        mr_dir = +1   # cheap → boost buy quote
    elif mid > HP_FAIR + HP_MR_THRESH:
        mr_dir = -1   # rich → boost sell quote

    bid_px = min(bb + 1, HP_FAIR - 1)
    ask_px = max(ba - 1, HP_FAIR + 1)

    # Pull toward zero: when long, buy less / sell more; when short, vice-versa.
    ratio = pos_after / HP_LIMIT
    buy_mult = max(0.0, 1.0 - HP_FLAT_PULL * ratio)
    sell_mult = max(0.0, 1.0 + HP_FLAT_PULL * ratio)

    # Apply MR boost on the alpha-side
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
