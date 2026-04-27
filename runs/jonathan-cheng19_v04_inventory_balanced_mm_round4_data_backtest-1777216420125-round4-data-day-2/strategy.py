# bump-for-ci: ensure v04/v05/v06 land on dashboard
"""Round 4 v04 — balanced two-sided MM with Avellaneda-Stoikov inventory sizing.

User spec: super-high Sharpe, flat-and-up PnL, no inventory drift, no
slippage, always post both sides at best_bid+1 / best_ask-1, "buy at the
bid, sell at the ask".

Key mechanism — INVENTORY-AWARE QUOTE SIZING (linear in position ratio):
   inv_ratio = position / limit   (range [-1, +1])
   buy_qsize  = BASE_QSIZE * (1 - inv_ratio)
   sell_qsize = BASE_QSIZE * (1 + inv_ratio)

When position = 0:  buy 30, sell 30  (symmetric, full size)
When position = +100 (half-long):  buy 15, sell 45  (3x more sell)
When position = +200 (max-long):   buy 0,  sell 60  (only sell, force unwind)

Effect: every fill instantly re-balances quote sizes so the next fill is
biased to bring position back toward zero. Position is mean-reverting,
never drifts to the rail except briefly. PnL is spread-capture × fill-rate,
not directional bets — naturally negative-skew.

Pure passive: no aggressive crossing. The only "take" actions are:
  - Fair-value taking — ask < fair (free PnL, no slippage)
  - Position-reducing-at-fair — take orders @ fair_int that flatten

VFE applies the same logic with dynamic fair = mid (no static anchor to
overfit, no Kalman MR to bleed on regime change like round-4 day-3).

VEV options: keep v02's BS+divergence pipeline (separate alpha source).

== ORIGINAL v02 NOTES BELOW ==

The v01 Mark-14/38 informed-follow signal was OVERFIT — after demeaning daily
drift, t-stats were all <2. Their +15-23 forward-return was an artifact of
multi-day drift, not real predictive alpha. v02 drops it.

HP strategy (matches user spec exactly):
  1. ALWAYS post passive bid at best_bid+1 (capped at fair-1, =9999) — be
     the new best bid. Always profitable: bid at 9999 vs fair 10000 → +1
     expected per fill.
  2. ALWAYS post passive ask at best_ask-1 (capped at fair+1, =10001) — be
     the new best ask. Always profitable: ask at 10001 vs fair 10000.
  3. Fair-value take: cross asks < 10000, bids > 10000 (free PnL when seen).
  4. Position-reducing-at-fair: take @ 10000 to flatten when needed.

VFE/VEV unchanged from v01.



Round 4 introduces named counterparties on `state.market_trades`. Mining the
3-day round-4 trade CSVs reveals two strongly-informed traders on HYDROGEL_PACK:

   sign-adjusted forward-mid move (sample sizes 496-515 each):
                   500-tick   2000-tick   5000-tick
   Mark 14 BUY     +1.12      +0.90       +15.71
   Mark 14 SELL    -2.35      -2.43       -23.70
   Mark 38 BUY     +2.43      +2.48       +23.32
   Mark 38 SELL    -0.79      -0.72       -15.06

When either trader BUYs, mid moves up ~20 ticks over the next 5000 ticks.
When they SELL, mid moves down ~20 ticks. This is enormous: HP std is ~30
across the whole 30k-tick window, so a 20-tick move from a single signal is
huge edge.

HP strategy:
  1. Aggressive MM — always penny-jump at best_bid+1 / best_ask-1 (capped
     at fair±1 so we never quote at-or-through fair).
  2. Fair-value taking — cross any ask < fair (=10000), any bid > fair.
  3. Position-reducing at fair — take orders priced at 10000 that move
     |position| toward zero.
  4. **INFORMED FOLLOW**: track Mark-14 + Mark-38 net signed volume on HP
     trades, decayed at INFORMED_DECAY (~5000-tick half-life to match alpha
     horizon). Apply target = INFORMED_GAIN * signal, clamped to ±limit.
     Drive position toward target via additional aggressive crossing — yes,
     this DOES pay the spread sometimes, but with 20-tick expected move
     that more than covers it.

VFE — keep v27 Kalman MR + size>=11 informed-flow.
VEV options — keep v27 Test_1-wide divergence trader.
"""

import json
import math
from typing import Any

from datamodel import (
    Listing, Observation, Order, OrderDepth, ProsperityEncoder, Symbol, Trade, TradingState,
)


# === Logger (visualizer-compatible) ============================================
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
HP_FAIR = 10000
HP_LIMIT = 200
HP_QSIZE = 30

# Informed-follow signal on HP
INFORMED_TRADERS_HP = {"Mark 14", "Mark 38"}  # validated via 3-day forward-return analysis
INFORMED_DECAY_HP = 0.9998  # ~5000-tick half-life — matches the alpha horizon
INFORMED_GAIN_HP = 8        # lots of position-target per signal unit (signal in lots)

# VFE Kalman MR (kept from v27)
VFE_LIMIT = 200
VFE_FAIR_STATIC = 5275
VFE_K_SS = 0.02
VFE_MR_GAIN = 2000
VFE_SIGMA_INIT = 15.0
VFE_TAKE_MAX_PAY = -2
VFE_QUOTE_EDGE = 1
VFE_QSIZE = 30

# VFE size-11 informed-flow signal (already validated in v12/v27)
VFE_INFORMED_SIZE = 11
VFE_INFORMED_GAIN = 10
VFE_INFORMED_DECAY = 0.998

# VEV options divergence (Test_1-wide thresholds, regime-robust)
VEV_LIMIT = 300
VEV_QSIZE = 30
VEV_TAKE_WIDTH = 1
VEV_ANCHOR_WARMUP = 100
VEV_DIVERGE_TAKE_SIZE = 30
VEV_THRESHOLDS = {
    4000: 25, 4500: 25, 5000: 22, 5100: 18, 5200: 14, 5300: 10, 5400: 5, 5500: 3,
}
VEV_MAX_DIVERGE_POS = 295


# === HYDROGEL_PACK pipeline =================================================
def update_hp_informed_signal(store, market_trades_hp):
    """Decayed signed-volume of Mark-14 + Mark-38 trades on HP."""
    sig = store.get("_inf", 0.0) * INFORMED_DECAY_HP
    for t in market_trades_hp or []:
        if t.buyer in INFORMED_TRADERS_HP:
            sig += t.quantity   # informed buy
        elif t.seller in INFORMED_TRADERS_HP:
            sig -= t.quantity   # informed sell
    store["_inf"] = sig
    return sig


def hp_orders(state, store):
    depth = state.order_depths.get("HYDROGEL_PACK")
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []

    position = state.position.get("HYDROGEL_PACK", 0)
    out = []
    bv = sv = 0

    # v02: dropped the Mark-14/38 informed-follow target chase (was overfit to
    # daily drift — t-stats <2 after demeaning). Pure passive MM now.

    # 3. Fair-value taking — any ask < fair, any bid > fair (free PnL).
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

    # 4. Position-reducing at fair — take orders @ 10000 that move toward zero.
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

    # 5. Always-on inventory-aware passive quoting (both sides every tick).
    #    Quote SIZES are asymmetric so position is pulled to zero on each fill.
    best_bid = max(depth.buy_orders); best_ask = min(depth.sell_orders)
    bid_px = min(best_bid + 1, HP_FAIR - 1)
    ask_px = max(best_ask - 1, HP_FAIR + 1)
    if bid_px < ask_px:
        # Inventory-aware sizing: shrink the side that would worsen position.
        pos_after_take = position + bv - sv
        inv_ratio = pos_after_take / HP_LIMIT  # in [-1, +1]
        buy_qsize = max(0, int(round(HP_QSIZE * (1.0 - inv_ratio))))
        sell_qsize = max(0, int(round(HP_QSIZE * (1.0 + inv_ratio))))
        buy_q = max(0, min(buy_qsize, HP_LIMIT - position - bv))
        sell_q = max(0, min(sell_qsize, HP_LIMIT + position - sv))
        if buy_q > 0:
            out.append(Order("HYDROGEL_PACK", bid_px, buy_q))
        if sell_q > 0:
            out.append(Order("HYDROGEL_PACK", ask_px, -sell_q))

    return out


# === VELVETFRUIT_EXTRACT pipeline (Kalman MR + informed flow, kept from v27) =
def update_vfe_informed_signal(store, market_trades_vfe, vfe_bid, vfe_ask):
    sig = store.get("_inf", 0.0) * VFE_INFORMED_DECAY
    for t in market_trades_vfe or []:
        if t.quantity < VFE_INFORMED_SIZE:
            continue
        if t.price >= vfe_ask:
            sig += t.quantity
        elif t.price <= vfe_bid:
            sig -= t.quantity
    store["_inf"] = sig
    return sig


def vfe_orders(state, store):
    """v04: VFE balanced two-sided MM with inventory-aware sizing.

    Dynamic fair = current mid (no static anchor to overfit, no Kalman MR
    to bleed on regime change like round-4 day-3).
    """
    depth = state.order_depths.get("VELVETFRUIT_EXTRACT")
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    bb = max(depth.buy_orders); ba = min(depth.sell_orders)
    fair = (bb + ba) / 2.0
    fair_int = round(fair)

    position = state.position.get("VELVETFRUIT_EXTRACT", 0)
    orders, bv, sv = [], 0, 0

    # 1. Fair-value taking (free PnL when ask < fair or bid > fair).
    for ask in sorted(depth.sell_orders):
        if ask >= fair_int: break
        avail = -depth.sell_orders[ask]
        qty = min(avail, VFE_LIMIT - position - bv)
        if qty <= 0: break
        orders.append(Order("VELVETFRUIT_EXTRACT", ask, qty)); bv += qty
    for bid in sorted(depth.buy_orders, reverse=True):
        if bid <= fair_int: break
        avail = depth.buy_orders[bid]
        qty = min(avail, VFE_LIMIT + position - sv)
        if qty <= 0: break
        orders.append(Order("VELVETFRUIT_EXTRACT", bid, -qty)); sv += qty

    # 2. Position-reducing-at-fair.
    pos_after = position + bv - sv
    if pos_after > 0 and fair_int in depth.buy_orders:
        avail = depth.buy_orders[fair_int]
        qty = min(avail, pos_after, VFE_LIMIT + position - sv)
        if qty > 0:
            orders.append(Order("VELVETFRUIT_EXTRACT", fair_int, -qty)); sv += qty
    if pos_after < 0 and fair_int in depth.sell_orders:
        avail = -depth.sell_orders[fair_int]
        qty = min(avail, -pos_after, VFE_LIMIT - position - bv)
        if qty > 0:
            orders.append(Order("VELVETFRUIT_EXTRACT", fair_int, qty)); bv += qty

    # 3. Inventory-aware passive quoting.
    bid_px = min(bb + 1, fair_int - 1)
    ask_px = max(ba - 1, fair_int + 1)
    if bid_px < ask_px:
        pos_after_take = position + bv - sv
        inv_ratio = pos_after_take / VFE_LIMIT
        buy_qsize = max(0, int(round(VFE_QSIZE * (1.0 - inv_ratio))))
        sell_qsize = max(0, int(round(VFE_QSIZE * (1.0 + inv_ratio))))
        buy_q = max(0, min(buy_qsize, VFE_LIMIT - position - bv))
        sell_q = max(0, min(sell_qsize, VFE_LIMIT + position - sv))
        if buy_q > 0:
            orders.append(Order("VELVETFRUIT_EXTRACT", bid_px, buy_q))
        if sell_q > 0:
            orders.append(Order("VELVETFRUIT_EXTRACT", ask_px, -sell_q))
    return orders


# === VEV options pipeline (zscore divergence trader, kept from v27) =========
def search_sells(depth):
    for p in sorted(depth.sell_orders):
        yield p, -depth.sell_orders[p]

def search_buys(depth):
    for p in sorted(depth.buy_orders, reverse=True):
        yield p, depth.buy_orders[p]

def full_depth_mid(depth):
    bids, asks = list(search_buys(depth)), list(search_sells(depth))
    bv, av = sum(v for _, v in bids), sum(v for _, v in asks)
    if bv <= 0 or av <= 0:
        return (max(depth.buy_orders) + min(depth.sell_orders)) / 2
    return (sum(p * v for p, v in bids) / bv + sum(p * v for p, v in asks) / av) / 2

def vev_orders_one(symbol, strike, state, store):
    depth = state.order_depths.get(symbol)
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    best_bid = max(depth.buy_orders); best_ask = min(depth.sell_orders)
    mid = (best_bid + best_ask) / 2
    fair = full_depth_mid(depth)
    scratch = store.setdefault(symbol, {})
    n = scratch.get("anchor_n", 0) + 1
    s = scratch.get("anchor_sum", 0.0) + mid
    scratch["anchor_n"], scratch["anchor_sum"] = n, s
    anchor = s / n
    position = state.position.get(symbol, 0)

    threshold = VEV_THRESHOLDS.get(strike, 0)
    out = []
    bought = sold = 0

    # Divergence-take
    if threshold > 0 and n >= VEV_ANCHOR_WARMUP:
        diverge = mid - anchor
        if abs(diverge) >= threshold:
            max_pos = VEV_MAX_DIVERGE_POS
            if diverge > 0 and position > -max_pos:
                room = position + max_pos
                for price, qty in search_buys(depth):
                    cap = min(VEV_LIMIT + position - sold, VEV_DIVERGE_TAKE_SIZE - sold, room - sold)
                    if cap <= 0: break
                    take = min(qty, cap)
                    out.append(Order(symbol, price, -take)); sold += take
            elif diverge < 0 and position < max_pos:
                room = max_pos - position
                for price, qty in search_sells(depth):
                    cap = min(VEV_LIMIT - position - bought, VEV_DIVERGE_TAKE_SIZE - bought, room - bought)
                    if cap <= 0: break
                    take = min(qty, cap)
                    out.append(Order(symbol, price, take)); bought += take

    pos_eff = position + bought - sold
    # Take orders vs full-depth fair
    for price, qty in search_sells(depth):
        if price >= fair - VEV_TAKE_WIDTH: break
        cap = VEV_LIMIT - pos_eff - bought
        if cap <= 0: break
        take = min(qty, cap)
        out.append(Order(symbol, price, take)); bought += take
    for price, qty in search_buys(depth):
        if price <= fair + VEV_TAKE_WIDTH: break
        cap = VEV_LIMIT + pos_eff - sold
        if cap <= 0: break
        take = min(qty, cap)
        out.append(Order(symbol, price, -take)); sold += take

    # MM quote
    qsize = VEV_QSIZE
    bid_px = min(math.floor((fair + best_bid) / 2), best_ask - 1)
    ask_px = max(math.ceil((fair + best_ask) / 2), best_bid + 1)
    buy = max(0, min(qsize, VEV_LIMIT - position - bought))
    sell = max(0, min(qsize, VEV_LIMIT + position - sold))
    if buy > 0 and bid_px < ask_px:
        out.append(Order(symbol, bid_px, buy))
    if sell > 0 and ask_px > bid_px:
        out.append(Order(symbol, ask_px, -sell))
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

        # HYDROGEL_PACK — aggressive MM + Mark-14/38 follow signal
        hp = hp_orders(state, store)
        if hp:
            orders["HYDROGEL_PACK"] = hp

        # VELVETFRUIT_EXTRACT — Kalman MR + size>=11 informed flow
        vfe = vfe_orders(state, store)
        if vfe:
            orders["VELVETFRUIT_EXTRACT"] = vfe

        # VEV options — zscore divergence (Test_1-wide thresholds)
        for K in (4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500):
            sym = f"VEV_{K}"
            ors = vev_orders_one(sym, K, state, store)
            if ors:
                orders[sym] = ors

        trader_data = json.dumps(store)
        logger.flush(state, orders, 0, trader_data)
        return orders, 0, trader_data
