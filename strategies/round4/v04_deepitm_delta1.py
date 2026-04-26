"""
Round 4 v04 — deep-ITM (VEV_4000, VEV_4500) priced as pure delta-1 vs
VELVETFRUIT spot.

Round-4 data shows TV = mid - max(0, S-K) ≈ 0 across every day for
strikes 4000 and 4500: these vouchers are behaving as direct claims on
S - K, not as options. The legacy anchor-divergence path treats them
like every other strike (running mean of own mid as anchor), which
ignores spot moves. Replace with a one-line fair = S - K, then market-
make tight around it (take when book offers below fair, quote inside
the spread).

Active strikes (5000-5500) and HYDROGEL/VELVETFRUIT untouched (= v01).
"""

import json
import math

from datamodel import Order, TradingState

TAKE_WIDTH = 1
ANCHOR_WARMUP = 100
DIVERGE_TAKE_SIZE = 30


# =========================================================================
# Shared book helpers
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


# =========================================================================
# Zscore pipeline (VEV_*)
# =========================================================================


def divergence_take_orders(cfg, depth, scratch, position, anchor, mid):
    threshold = cfg.get("diverge_threshold", 0)
    if threshold <= 0 or scratch.get("anchor_n", 0) < ANCHOR_WARMUP:
        return [], 0, 0
    diverge = mid - anchor
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
            out.append(Order(product, price, -take))
            sold += take
    elif diverge < 0 and position < max_pos:
        room = max_pos - position
        for price, qty in search_sells(depth):
            cap = min(limit - position - bought, DIVERGE_TAKE_SIZE - bought, room - bought)
            if cap <= 0:
                break
            take = min(qty, cap)
            out.append(Order(product, price, take))
            bought += take
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
        out.append(Order(product, price, take))
        bought += take
    for price, qty in search_buys(depth):
        if price <= fair + TAKE_WIDTH:
            break
        cap = limit + position - sold
        if cap <= 0:
            break
        take = min(qty, cap)
        out.append(Order(product, price, -take))
        sold += take
    return out, bought, sold


def make_quote(cfg, fair, best_bid, best_ask, position, bought, sold):
    product, limit = cfg["product"], cfg["position_limit"]
    qsize = cfg.get("quote_size", 20)
    # Quote at midpoint between fair and the touch on each side.
    bid_px = min(math.floor((fair + best_bid) / 2), best_ask - 1)
    ask_px = max(math.ceil((fair + best_ask) / 2), best_bid + 1)
    buy = max(0, min(qsize, limit - position - bought))
    sell = max(0, min(qsize, limit + position - sold))
    out = []
    if buy > 0 and bid_px < ask_px:
        out.append(Order(product, bid_px, buy))
    if sell > 0 and ask_px > bid_px:
        out.append(Order(product, ask_px, -sell))
    return out


def zscore_orders(cfg, state, scratch):
    depth = state.order_depths.get(cfg["product"])
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []

    best_bid = max(depth.buy_orders)
    best_ask = min(depth.sell_orders)
    mid = (best_bid + best_ask) / 2
    fair = full_depth_mid(depth)

    n = scratch.get("anchor_n", 0) + 1
    s = scratch.get("anchor_sum", 0.0) + mid
    scratch["anchor_n"], scratch["anchor_sum"] = n, s
    anchor = s / n
    position = state.position.get(cfg["product"], 0)

    diverge, d_bought, d_sold = divergence_take_orders(
        cfg, depth, scratch, position, anchor, mid
    )
    pos_eff = position + d_bought - d_sold
    takes, bought, sold = take_orders(cfg, depth, fair, pos_eff)
    bought += d_bought
    sold += d_sold
    quotes = make_quote(cfg, fair, best_bid, best_ask, position, bought, sold)
    return diverge + takes + quotes


# =========================================================================
# Delta-1 pipeline (deep-ITM VEV_4000, VEV_4500)
# =========================================================================


def delta1_orders(cfg, state, S):
    """fair := max(0, S - K). Take everything below fair (buy) / above (sell)
    aggressively, then quote a tight spread around fair."""
    depth = state.order_depths.get(cfg["product"])
    if not depth or not depth.buy_orders or not depth.sell_orders or S <= 0:
        return []
    K = cfg["strike"]
    fair = max(0.0, S - K)
    position = state.position.get(cfg["product"], 0)

    takes, bought, sold = take_orders(cfg, depth, fair, position)
    best_bid = max(depth.buy_orders)
    best_ask = min(depth.sell_orders)
    quotes = make_quote(cfg, fair, best_bid, best_ask, position, bought, sold)
    return takes + quotes


# =========================================================================
# Kalman-MR pipeline (HYDROGEL_PACK, VELVETFRUIT_EXTRACT)
# =========================================================================


def kalman_mr_orders(cfg, depth, position, scratch):
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    product = cfg["product"]
    limit = cfg["position_limit"]
    bb = max(depth.buy_orders)
    ba = min(depth.sell_orders)
    bv_tob = depth.buy_orders[bb]
    av_tob = -depth.sell_orders[ba]
    tot = bv_tob + av_tob
    micro = (bb * av_tob + ba * bv_tob) / tot if tot > 0 else (bb + ba) / 2.0
    mid = (bb + ba) / 2.0

    # Kalman-track fair on volume-weighted micro-price.
    k_ss = cfg["k_ss"]
    fair = scratch.get("_f", micro)
    innov = micro - fair
    err_ema = scratch.get("_err", abs(innov))
    err_ema += k_ss * (abs(innov) - err_ema)
    fair += (k_ss / (1.0 + err_ema)) * innov
    scratch["_f"], scratch["_err"] = fair, err_ema

    # Online σ estimate from (mid - fair) variance.
    n = scratch.get("_n", 0) + 1
    s2 = scratch.get("_s2", 0.0) + (mid - fair) ** 2
    scratch["_n"], scratch["_s2"] = n, s2
    sigma = max(1.0, (s2 / n) ** 0.5) if n > 50 else cfg["sigma_init"]

    # target = mr_gain · (anchor − mid) / σ, clamped to ±limit.
    # (Equivalently the sum of short-term and long-term reversion terms.)
    anchor = cfg["fair_static"]
    target = max(-limit, min(limit, round(cfg["mr_gain"] * (anchor - mid) / sigma)))

    take_max_pay = cfg["take_max_pay"]
    quote_edge = cfg["quote_edge"]
    quote_size = cfg["quote_size"]

    orders = []
    bv = sv = 0
    delta = target - position

    if delta > 0:
        for a in sorted(depth.sell_orders):
            if a > fair + take_max_pay:
                break
            room = min(-depth.sell_orders[a], delta - bv, limit - position - bv)
            if room <= 0:
                break
            orders.append(Order(product, a, room))
            bv += room
    elif delta < 0:
        need = -delta
        for b in sorted(depth.buy_orders, reverse=True):
            if b < fair - take_max_pay:
                break
            room = min(depth.buy_orders[b], need - sv, limit + position - sv)
            if room <= 0:
                break
            orders.append(Order(product, b, -room))
            sv += room

    baaf = min((p for p in depth.sell_orders if p >= fair + quote_edge), default=None)
    bbbf = max((p for p in depth.buy_orders if p <= fair - quote_edge), default=None)
    if bbbf is not None:
        buy_q = min(quote_size, limit - position - bv)
        if buy_q > 0:
            orders.append(Order(product, bbbf + 1, buy_q))
    if baaf is not None:
        sell_q = min(quote_size, limit + position - sv)
        if sell_q > 0:
            orders.append(Order(product, baaf - 1, -sell_q))

    return orders


# =========================================================================
# Per-product configuration
# =========================================================================

KALMAN_MR_PRODUCTS = [
    {
        "product": "HYDROGEL_PACK",
        "position_limit": 200,
        "k_ss": 0.02,
        "fair_static": 10030,       # mean+40; mean across 3 days = 9990
        "mr_gain": 2000,
        "sigma_init": 30.0,
        "take_max_pay": -6,         # only cross when offer ≥6 ticks below fair
        "quote_edge": 3,
        "quote_size": 30,
    },
    {
        "product": "VELVETFRUIT_EXTRACT",
        "position_limit": 200,
        "k_ss": 0.02,
        "fair_static": 5275,        # mean+25; mean across 3 days = 5250
        "mr_gain": 2000,
        "sigma_init": 15.0,
        "take_max_pay": -2,         # only cross when offer ≥2 ticks below fair
        "quote_edge": 1,
        "quote_size": 30,
    },
]

ZSCORE_PRODUCTS = [
    # Active strikes only — deep ITM moved to DELTA1_PRODUCTS.
    {"product": "VEV_5000", "position_limit": 300, "quote_size": 30, "diverge_threshold": 18, "max_diverge_position": 295},
    {"product": "VEV_5100", "position_limit": 300, "quote_size": 30, "diverge_threshold": 14, "max_diverge_position": 295},
    {"product": "VEV_5200", "position_limit": 300, "quote_size": 30, "diverge_threshold": 11, "max_diverge_position": 295},
    {"product": "VEV_5300", "position_limit": 300, "quote_size": 30, "diverge_threshold": 8, "max_diverge_position": 295},
    {"product": "VEV_5400", "position_limit": 300, "quote_size": 30, "diverge_threshold": 4, "max_diverge_position": 295},
    {"product": "VEV_5500", "position_limit": 300, "quote_size": 30, "diverge_threshold": 2, "max_diverge_position": 295},
]

DELTA1_PRODUCTS = [
    {"product": "VEV_4000", "strike": 4000, "position_limit": 300, "quote_size": 30},
    {"product": "VEV_4500", "strike": 4500, "position_limit": 300, "quote_size": 30},
]

VFRUIT = "VELVETFRUIT_EXTRACT"


class Trader:
    def bid(self):
        return 0

    def run(self, state: TradingState):
        try:
            store = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            store = {}

        orders: dict[str, list[Order]] = {}

        for cfg in KALMAN_MR_PRODUCTS:
            depth = state.order_depths.get(cfg["product"])
            ors = kalman_mr_orders(cfg, depth, state.position.get(cfg["product"], 0),
                                   store.setdefault(cfg["product"], {}))
            if ors:
                orders[cfg["product"]] = ors

        # Spot for delta-1 pricing.
        vf_depth = state.order_depths.get(VFRUIT)
        if vf_depth and vf_depth.buy_orders and vf_depth.sell_orders:
            S = (max(vf_depth.buy_orders) + min(vf_depth.sell_orders)) / 2.0
            store["_S_last"] = S
        else:
            S = store.get("_S_last", 0.0)

        for cfg in DELTA1_PRODUCTS:
            ors = delta1_orders(cfg, state, S)
            if ors:
                orders[cfg["product"]] = ors

        for cfg in ZSCORE_PRODUCTS:
            ors = zscore_orders(cfg, state, store.setdefault(cfg["product"], {}))
            if ors:
                orders[cfg["product"]] = ors

        return orders, 0, json.dumps(store)
