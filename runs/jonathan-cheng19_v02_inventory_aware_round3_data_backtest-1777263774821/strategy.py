"""Round 3 v02 — v01 + Stoikov-style inventory skew + bot-flow-aware exit-at-fair.

What's new vs v01
-----------------
1. **Stoikov-style reservation-price skew on VEV quotes.**
   Avellaneda-Stoikov (2008) optimal MM under inventory risk: shift the
   reservation price away from fair in the direction opposite the inventory.
   Implemented as one tick per `q / SKEW_PER_LOT_INV` lots; both sides of the
   quote shift together so fills on the *unwind* side become more likely
   while the *add* side becomes a worse price for the counterparty (queue
   depth effectively widens). Standard P3-finalist pattern (jmerle's MM
   template ships a constant-spread skew of this form).

2. **Bot-flow-aware exit-at-fair.**
   The user's explicit rule: when we can unload at fair without paying spread
   and the flow signal does *not* indicate a continuation in our favour,
   take the exit. Concretely:
     - If position > 0 AND best_bid >= round(fair) AND flow <= +threshold,
       cross the bid for min(position, best_bid_volume) at best_bid.
     - Symmetric for short.
   Flow `>` threshold means informed buyers are present, so expected drift
   is *up* and holding the long is the better trade — skip the exit.
   Flow `<` -threshold means informed sellers, drift is *down*, holding the
   short is better.

3. **Inventory-aware skew modulation by flow.**
   When flow agrees with our inventory direction (e.g. long + buying flow),
   downscale the skew so we keep accumulating cheap. When flow disagrees,
   upscale to unload faster.

Parameters added vs v01:
    SKEW_PER_LOT_INV = 25   (1 tick of skew per ~25 lots of inventory)
    EXIT_AT_FAIR     = True (toggle for the unload-at-fair rule)
    FLOW_AGREE_K     = 0.4  (skew multiplier when flow agrees)
    FLOW_DISAGREE_K  = 1.6  (skew multiplier when flow disagrees)

Everything else (Kalman-MR underlier configs, BS smile σ, FLOW_DECAY,
diverge thresholds, T_EXPIRY, position limits) is identical to v01.
"""

import json
import math
from typing import Any

from datamodel import (
    Listing,
    Observation,
    Order,
    OrderDepth,
    ProsperityEncoder,
    Symbol,
    Trade,
    TradingState,
)


# =========================================================================
# Logger — IMC P4 Visualizer compatible
# =========================================================================


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


# =========================================================================
# Black-Scholes (pure Python — scipy not available in submission env)
# =========================================================================


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S, K, T, sigma):
    if T <= 0 or sigma <= 0 or S <= 0:
        return max(S - K, 0.0)
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    return S * _norm_cdf(d1) - K * _norm_cdf(d2)


# =========================================================================
# Calibration constants
# =========================================================================

T_EXPIRY = 30_000
TICK_STEP = 100

SIGMA_SMILE = {
    4000: 0.0008960, 4500: 0.0004921, 5000: 0.0002616, 5100: 0.0002558,
    5200: 0.0002671, 5300: 0.0002705, 5400: 0.0002515, 5500: 0.0002697,
}

# Test_1 frozen
TAKE_WIDTH = 1
ANCHOR_WARMUP = 100
DIVERGE_TAKE_SIZE = 30
FLOW_DECAY = 0.92

# v02 — inventory management constants
SKEW_PER_LOT_INV = 25     # 1 tick of skew per 25 lots of inventory (Stoikov-style)
FLOW_AGREE_K = 0.4        # downscale skew when flow agrees with inventory
FLOW_DISAGREE_K = 1.6     # upscale skew when flow disagrees
EXIT_AT_FAIR = True       # opportunistic unload-at-fair on flow reversal
FLOW_REVERSAL = 2.0       # |flow| threshold to treat as a reversal signal
MIN_INVENTORY_FOR_EXIT = 30  # only exit when |position| >= 30 lots


# =========================================================================
# Helpers
# =========================================================================


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


def microprice(depth):
    bb = max(depth.buy_orders); ba = min(depth.sell_orders)
    bv = depth.buy_orders[bb]; av = -depth.sell_orders[ba]
    tot = bv + av
    return (bb * av + ba * bv) / tot if tot > 0 else (bb + ba) / 2.0


def update_flow(scratch, market_trades, bb, ba):
    flow = scratch.get("_flow", 0.0) * FLOW_DECAY
    for t in market_trades or []:
        if t.price >= ba:
            flow += t.quantity
        elif t.price <= bb:
            flow -= t.quantity
    scratch["_flow"] = flow
    return flow


def inventory_skew(position: int, flow: float) -> int:
    """Stoikov-style inventory skew, modulated by flow agreement.

    Returns a SIGNED tick offset to add to fair price. Long → negative
    (bid further below fair, ask further below fair → encourage selling).
    """
    base = -position / SKEW_PER_LOT_INV
    if (position > 0 and flow > 1.0) or (position < 0 and flow < -1.0):
        base *= FLOW_AGREE_K       # flow supports our position → light skew
    elif (position > 0 and flow < -1.0) or (position < 0 and flow > 1.0):
        base *= FLOW_DISAGREE_K    # flow against → strong skew (unload)
    return int(round(base))


def exit_at_fair_orders(symbol, depth, position, fair, flow, limit, bv, sv):
    """Cross the book to exit position when bid/ask sits at-or-better-than fair
    AND flow is actively *against* the inventory (predicting reversal so the
    re-entry will be cheaper). Conservative: requires |flow| > FLOW_REVERSAL
    AND |position| > MIN_INVENTORY_FOR_EXIT."""
    if not EXIT_AT_FAIR or position == 0:
        return [], 0, 0
    if abs(position) < MIN_INVENTORY_FOR_EXIT:
        return [], 0, 0
    fair_int = int(round(fair))
    out, b, s = [], 0, 0
    if position > 0 and flow < -FLOW_REVERSAL:
        # long + selling flow → expect drop, exit now to re-enter lower
        for px, qty in search_buys(depth):
            if px < fair_int:
                break
            cap = min(qty, position - s, limit + position - sv - s)
            if cap <= 0:
                break
            out.append(Order(symbol, px, -cap)); s += cap
            if s >= position:
                break
    elif position < 0 and flow > FLOW_REVERSAL:
        need = -position
        for px, qty in search_sells(depth):
            if px > fair_int:
                break
            cap = min(qty, need - b, limit - position - bv - b)
            if cap <= 0:
                break
            out.append(Order(symbol, px, cap)); b += cap
            if b >= need:
                break
    return out, b, s


# =========================================================================
# VEV pipeline (v01 + Stoikov skew + exit-at-fair)
# =========================================================================


def divergence_take_orders(cfg, depth, scratch, position, anchor, mid):
    threshold = cfg.get("diverge_threshold", 0)
    flow = scratch.get("_flow", 0.0)
    diverge = mid - anchor
    if diverge > 0 and flow > 1.0:
        threshold = max(1, threshold - 1)
    elif diverge < 0 and flow < -1.0:
        threshold = max(1, threshold - 1)
    elif diverge > 0 and flow < -1.0:
        threshold = threshold + 2
    elif diverge < 0 and flow > 1.0:
        threshold = threshold + 2

    if threshold <= 0 or scratch.get("anchor_n", 0) < ANCHOR_WARMUP:
        return [], 0, 0
    if abs(diverge) < threshold:
        return [], 0, 0

    product, limit = cfg["product"], cfg["position_limit"]
    max_pos = cfg.get("max_diverge_position", 60)
    out, bought, sold = [], 0, 0
    if diverge > 0 and position > -max_pos:
        room = position + max_pos
        for price, qty in search_buys(depth):
            cap = min(limit + position - sold, DIVERGE_TAKE_SIZE - sold, room - sold)
            if cap <= 0:
                break
            take = min(qty, cap)
            out.append(Order(product, price, -take)); sold += take
    elif diverge < 0 and position < max_pos:
        room = max_pos - position
        for price, qty in search_sells(depth):
            cap = min(limit - position - bought, DIVERGE_TAKE_SIZE - bought, room - bought)
            if cap <= 0:
                break
            take = min(qty, cap)
            out.append(Order(product, price, take)); bought += take
    return out, bought, sold


def take_orders(cfg, depth, fair, position):
    product, limit = cfg["product"], cfg["position_limit"]
    out, bought, sold = [], 0, 0
    for price, qty in search_sells(depth):
        if price >= fair - TAKE_WIDTH:
            break
        cap = limit - position - bought
        if cap <= 0:
            break
        take = min(qty, cap)
        out.append(Order(product, price, take)); bought += take
    for price, qty in search_buys(depth):
        if price <= fair + TAKE_WIDTH:
            break
        cap = limit + position - sold
        if cap <= 0:
            break
        take = min(qty, cap)
        out.append(Order(product, price, -take)); sold += take
    return out, bought, sold


def make_quote(cfg, fair, best_bid, best_ask, position, bought, sold, skew):
    """Quote with Stoikov skew added to both legs."""
    product, limit = cfg["product"], cfg["position_limit"]
    qsize = cfg.get("quote_size", 20)
    fair_skewed = fair + skew  # both legs shift together
    bid_px = min(math.floor((fair_skewed + best_bid) / 2), best_ask - 1)
    ask_px = max(math.ceil((fair_skewed + best_ask) / 2), best_bid + 1)
    buy = max(0, min(qsize, limit - position - bought))
    sell = max(0, min(qsize, limit + position - sold))
    out = []
    if buy > 0 and bid_px < ask_px:
        out.append(Order(product, bid_px, buy))
    if sell > 0 and ask_px > bid_px:
        out.append(Order(product, ask_px, -sell))
    return out


def vev_orders(cfg, state, scratch, vev_S, ttx):
    depth = state.order_depths.get(cfg["product"])
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []

    best_bid = max(depth.buy_orders)
    best_ask = min(depth.sell_orders)
    mid = (best_bid + best_ask) / 2
    fair = full_depth_mid(depth)

    # BS-with-smile anchor
    K = cfg["strike"]
    sigma_K = SIGMA_SMILE.get(K, 0.000265)
    anchor = bs_call(vev_S, K, ttx, sigma_K)

    # Warmup counter
    n = scratch.get("anchor_n", 0) + 1
    scratch["anchor_n"] = n

    # Flow EMA
    flow = update_flow(scratch, state.market_trades.get(cfg["product"], []), best_bid, best_ask)

    position = state.position.get(cfg["product"], 0)
    limit = cfg["position_limit"]

    # 1. Opportunistic exit at fair (bot-flow gated)
    exit_o, e_b, e_s = exit_at_fair_orders(cfg["product"], depth, position, fair, flow, limit, 0, 0)
    pos_after_exit = position + e_b - e_s

    # 2. Divergence take (anchor = BS)
    diverge, d_b, d_s = divergence_take_orders(cfg, depth, scratch, pos_after_exit, anchor, mid)
    pos_after_diverge = pos_after_exit + d_b - d_s

    # 3. Standard take vs full-depth fair
    takes, t_b, t_s = take_orders(cfg, depth, fair, pos_after_diverge)
    bought = e_b + d_b + t_b
    sold = e_s + d_s + t_s

    # 4. MM with Stoikov inventory skew
    skew = inventory_skew(position + bought - sold, flow)
    quotes = make_quote(cfg, fair, best_bid, best_ask, position, bought, sold, skew)
    return exit_o + diverge + takes + quotes


# =========================================================================
# Underlier pipeline (frozen verbatim from Test_1 / v01)
# =========================================================================


KALMAN_MR_PRODUCTS = [
    {"product": "HYDROGEL_PACK", "position_limit": 100, "k_ss": 0.02,
     "fair_static": 10030, "mr_gain": 2000, "sigma_init": 30.0,
     "take_max_pay": -6, "quote_edge": 3, "quote_size": 30},
    {"product": "VELVETFRUIT_EXTRACT", "position_limit": 100, "k_ss": 0.02,
     "fair_static": 5275, "mr_gain": 2000, "sigma_init": 15.0,
     "take_max_pay": -2, "quote_edge": 1, "quote_size": 30},
]


def kalman_mr_orders(cfg, depth, position, scratch):
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    product, limit = cfg["product"], cfg["position_limit"]
    bb = max(depth.buy_orders); ba = min(depth.sell_orders)
    bv_tob = depth.buy_orders[bb]; av_tob = -depth.sell_orders[ba]
    tot = bv_tob + av_tob
    micro = (bb * av_tob + ba * bv_tob) / tot if tot > 0 else (bb + ba) / 2.0
    mid = (bb + ba) / 2.0

    k_ss = cfg["k_ss"]
    fair = scratch.get("_f", micro)
    innov = micro - fair
    err_ema = scratch.get("_err", abs(innov))
    err_ema += k_ss * (abs(innov) - err_ema)
    fair += (k_ss / (1.0 + err_ema)) * innov
    scratch["_f"], scratch["_err"] = fair, err_ema

    n = scratch.get("_n", 0) + 1
    s2 = scratch.get("_s2", 0.0) + (mid - fair) ** 2
    scratch["_n"], scratch["_s2"] = n, s2
    sigma = max(1.0, (s2 / n) ** 0.5) if n > 50 else cfg["sigma_init"]

    anchor = cfg["fair_static"]
    target = max(-limit, min(limit, round(cfg["mr_gain"] * (anchor - mid) / sigma)))

    take_max_pay = cfg["take_max_pay"]
    quote_edge = cfg["quote_edge"]
    quote_size = cfg["quote_size"]

    orders, bv, sv = [], 0, 0
    delta = target - position
    if delta > 0:
        for a in sorted(depth.sell_orders):
            if a > fair + take_max_pay: break
            room = min(-depth.sell_orders[a], delta - bv, limit - position - bv)
            if room <= 0: break
            orders.append(Order(product, a, room)); bv += room
    elif delta < 0:
        need = -delta
        for b in sorted(depth.buy_orders, reverse=True):
            if b < fair - take_max_pay: break
            room = min(depth.buy_orders[b], need - sv, limit + position - sv)
            if room <= 0: break
            orders.append(Order(product, b, -room)); sv += room

    baaf = min((p for p in depth.sell_orders if p >= fair + quote_edge), default=None)
    bbbf = max((p for p in depth.buy_orders if p <= fair - quote_edge), default=None)
    if bbbf is not None:
        buy_q = min(quote_size, limit - position - bv)
        if buy_q > 0: orders.append(Order(product, bbbf + 1, buy_q))
    if baaf is not None:
        sell_q = min(quote_size, limit + position - sv)
        if sell_q > 0: orders.append(Order(product, baaf - 1, -sell_q))
    return orders


# =========================================================================
# VEV per-product config
# =========================================================================


VEV_PRODUCTS = [
    {"product": "VEV_4000", "strike": 4000, "position_limit": 100, "quote_size": 30, "diverge_threshold": 25, "max_diverge_position": 95},
    {"product": "VEV_4500", "strike": 4500, "position_limit": 100, "quote_size": 30, "diverge_threshold": 25, "max_diverge_position": 95},
    {"product": "VEV_5000", "strike": 5000, "position_limit": 100, "quote_size": 30, "diverge_threshold": 22, "max_diverge_position": 95},
    {"product": "VEV_5100", "strike": 5100, "position_limit": 100, "quote_size": 30, "diverge_threshold": 18, "max_diverge_position": 95},
    {"product": "VEV_5200", "strike": 5200, "position_limit": 100, "quote_size": 30, "diverge_threshold": 14, "max_diverge_position": 95},
    {"product": "VEV_5300", "strike": 5300, "position_limit": 100, "quote_size": 30, "diverge_threshold": 10, "max_diverge_position": 95},
    {"product": "VEV_5400", "strike": 5400, "position_limit": 100, "quote_size": 30, "diverge_threshold": 5,  "max_diverge_position": 95},
    {"product": "VEV_5500", "strike": 5500, "position_limit": 100, "quote_size": 30, "diverge_threshold": 3,  "max_diverge_position": 95},
]


# =========================================================================
# Trader
# =========================================================================


class Trader:
    def bid(self) -> int:
        return 0

    def run(self, state: TradingState):
        try:
            store = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            store = {}

        orders: dict[str, list[Order]] = {}

        for cfg in KALMAN_MR_PRODUCTS:
            depth = state.order_depths.get(cfg["product"])
            ors = kalman_mr_orders(
                cfg, depth, state.position.get(cfg["product"], 0),
                store.setdefault(cfg["product"], {}),
            )
            if ors:
                orders[cfg["product"]] = ors

        vfe_depth = state.order_depths.get("VELVETFRUIT_EXTRACT")
        if vfe_depth and vfe_depth.buy_orders and vfe_depth.sell_orders:
            vev_S = microprice(vfe_depth)
            ttx = max(1.0, T_EXPIRY - state.timestamp / TICK_STEP)
            for cfg in VEV_PRODUCTS:
                ors = vev_orders(cfg, state, store.setdefault(cfg["product"], {}), vev_S, ttx)
                if ors:
                    orders[cfg["product"]] = ors

        trader_data = json.dumps(store)
        logger.flush(state, orders, 0, trader_data)
        return orders, 0, trader_data
