"""
Round 4 — spot_regime + position-aware divergence threshold.

Single-layer alternative to active-unwind (which thrashed against
divergence). When position is already large, RAISE the divergence
threshold so no MORE accumulation happens — without actively selling.
Existing positions hold; new accumulation paused when over-loaded.

Effective threshold:
  base × (1 + POS_K × |position|/limit)
  POS_K = 2.0 → at full limit, threshold triples; at 0 position, no change.

Single-layer = no thrashing. Unlike a regime gate, it doesn't kill the
divergence signal in calm regimes — only when WE specifically have
loaded too much.

Result vs frontier (QP=1.0):
                       D1       D2       D3      mean      min   mean+min
  softer_vfruit  244,058  208,026   54,358  168,814   54,358    223,172  ← best mean
  spot_regime    235,320  160,649   65,790  153,920   65,790    219,710  ← best mean+min
  position_throttle 170,139  133,240   76,969  126,782  76,969    203,751  ← BEST MIN

Trade-off: gives up $27k of mean to gain $11k of min vs spot_regime,
or $42k mean for $22k min vs softer_vfruit. Pareto-frontier addition,
not dominator. Pick this strategy if min-day defense is the priority.
"""

import json
import math

from datamodel import Order, TradingState

TAKE_WIDTH = 1
ANCHOR_WARMUP = 100
DIVERGE_TAKE_SIZE = 30
VFRUIT = "VELVETFRUIT_EXTRACT"


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


POS_K = 2.0  # threshold scales 1×→3× as |position| goes 0→limit


def divergence_take_orders(cfg, depth, scratch, position, anchor, mid, regime_scale=1.0):
    base_threshold = cfg.get("diverge_threshold", 0)
    limit = cfg.get("position_limit", 1)
    pos_factor = 1.0 + POS_K * abs(position) / max(1, limit)
    threshold = base_threshold * pos_factor
    if threshold <= 0 or scratch.get("anchor_n", 0) < ANCHOR_WARMUP:
        return [], 0, 0
    diverge = mid - anchor
    if abs(diverge) < threshold:
        return [], 0, 0

    product, limit = cfg["product"], cfg["position_limit"]
    max_pos = max(1, int(cfg.get("max_diverge_position", 60) * regime_scale))
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


def zscore_orders(cfg, state, scratch, regime_scale=1.0):
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
        cfg, depth, scratch, position, anchor, mid, regime_scale
    )
    pos_eff = position + d_bought - d_sold
    takes, bought, sold = take_orders(cfg, depth, fair, pos_eff)
    bought += d_bought
    sold += d_sold
    quotes = make_quote(cfg, fair, best_bid, best_ask, position, bought, sold)
    return diverge + takes + quotes


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
        "mr_gain": 1000,            # halved (weak MR in round 4: ADF rejects 4% only)
        "sigma_init": 15.0,
        "take_max_pay": -4,         # raised from -2 (only cross on bigger dislocations)
        "quote_edge": 1,
        "quote_size": 15,           # halved
    },
]

ZSCORE_PRODUCTS = [
    {"product": "VEV_4000", "position_limit": 300, "quote_size": 30, "diverge_threshold": 20, "max_diverge_position": 295},
    {"product": "VEV_4500", "position_limit": 300, "quote_size": 30, "diverge_threshold": 20, "max_diverge_position": 295},
    {"product": "VEV_5000", "position_limit": 300, "quote_size": 30, "diverge_threshold": 18, "max_diverge_position": 295},
    {"product": "VEV_5100", "position_limit": 300, "quote_size": 30, "diverge_threshold": 14, "max_diverge_position": 295},
    {"product": "VEV_5200", "position_limit": 300, "quote_size": 30, "diverge_threshold": 11, "max_diverge_position": 295},
    {"product": "VEV_5300", "position_limit": 300, "quote_size": 30, "diverge_threshold": 8, "max_diverge_position": 295},
    {"product": "VEV_5400", "position_limit": 300, "quote_size": 30, "diverge_threshold": 4, "max_diverge_position": 295},
    {"product": "VEV_5500", "position_limit": 300, "quote_size": 30, "diverge_threshold": 2, "max_diverge_position": 295},
]
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

        # Regime detection via 200-tick rolling spot z-score.
        # |z| > 1.5 → spot is far from rolling mean → divergence positions
        # are loaded against the eventual reversion → scale max position
        # down 1.0 → 0.30 linearly. Scale stays at 1.0 when spot is calm.
        S = 0.0
        vf_depth = state.order_depths.get(VFRUIT)
        if vf_depth and vf_depth.buy_orders and vf_depth.sell_orders:
            S = (max(vf_depth.buy_orders) + min(vf_depth.sell_orders)) / 2.0
        regime_scale = 1.0
        if S > 0:
            spot_buf = store.setdefault("_spot_buf", [])
            spot_buf.append(S)
            if len(spot_buf) > 200:
                del spot_buf[0]
            if len(spot_buf) >= 100:
                mu_w = sum(spot_buf) / len(spot_buf)
                var_w = sum((x - mu_w) ** 2 for x in spot_buf) / len(spot_buf)
                sd_w = math.sqrt(max(1e-6, var_w))
                if sd_w > 0.5:
                    z = abs(S - mu_w) / sd_w
                    if z >= 1.5:
                        regime_scale = 0.30
                    elif z > 0.5:
                        regime_scale = 1.0 - 0.70 * (z - 0.5)

        for cfg in ZSCORE_PRODUCTS:
            ors = zscore_orders(cfg, state, store.setdefault(cfg["product"], {}),
                                regime_scale)
            if ors:
                orders[cfg["product"]] = ors

        return orders, 0, json.dumps(store)
