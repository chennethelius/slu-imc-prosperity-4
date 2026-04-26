"""
Round 3 submission — two pipelines, one Trader.

  HYDROGEL_PACK            → proportional MR + Kalman fair  (kalman_mr)
  VELVETFRUIT_EXTRACT      → proportional MR + Kalman fair  (kalman_mr)
  VEV_4000 … VEV_5500      → anchor-divergence + market-make (zscore)

PRE-SUBMISSION CHECKLIST:
  - Re-sweep fair_static for HYDROGEL_PACK and VELVETFRUIT_EXTRACT against
    the latest available data (round-N day_0..day_{N-1} CSVs).
  - Score each candidate by per-day mean AND min PnL — IMC submissions
    only run a single day, so a high-mean param that bombs on one day
    is worse than a flatter one. Don't pick the global-sum optimum.
  - Sanity check: rerun against the most recent real Prosperity sandbox
    log with --max-timestamp matching the sandbox length. Compare
    per-product PnL within ~5% of reality.

Both delta-1 products (HYDROGEL, VELVETFRUIT) are mean-reverting at every
horizon (variance ratios <1) so the Kalman + proportional-MR engine is
the right tool. VEVs use the existing anchor-divergence pipeline because
they have crash regimes where threshold-take wins.

Kalman-MR pipeline:
  1. Kalman-track fair from the volume-weighted touch micro-price
  2. Long-term anchor = fair_static (data-derived true mean), optionally
     blended with a slow EMA of mid (anchor_alpha) to track day-level drift.
     HYDROGEL has stable mean across days → pure static.
     VELVETFRUIT has +5/day mean drift → static + slow EMA.
  3. target = MR_GAIN · ((fair − mid) + (anchor − fair)) / σ
     — short-term reversion + long-term anchor pull, both scaled by σ
  4. Cross book up to fair + TAKE_MAX_PAY (0 = at-or-below fair only)
  5. Post passive quotes one tick inside the nearest level at fair ± quote_edge.
     HYDROGEL (wide spread 16, thin book) → quote_edge=3 stays clear of touch.
     VELVETFRUIT (narrow spread 5, deep book) → quote_edge=1 quotes inside.

Zscore pipeline:
  1. fair = full_depth_mid + aggressor_lambda · rolling aggressor flow
  2. anchor = expanding-window mean of mids
  3. if |mid − anchor| ≥ diverge_threshold: aggressively take the
     mean-reverting side
  4. Standard take + inventory-skewed bid/ask quotes
"""

import json
import math

from datamodel import Order, OrderDepth, TradingState


# =========================================================================
# zscore (multi-product anchor-divergence) constants & helpers
# =========================================================================

SPREAD_FRACTION = 0.5
SKEW_PER_UNIT = 0.02
VOL_WINDOW = 100
VOL_SCALE_MAX = 2.0
TAKE_WIDTH = 1
AGGRESSOR_WINDOW = 10
ANCHOR_WARMUP = 100
DIVERGE_TAKE_SIZE = 30


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


def realized_vol(mids):
    if len(mids) < 2:
        return 0.0
    diffs = [mids[i] - mids[i - 1] for i in range(1, len(mids))]
    mean = sum(diffs) / len(diffs)
    return math.sqrt(sum((d - mean) ** 2 for d in diffs) / len(diffs))


def aggressor_side(price, best_bid, best_ask):
    return 1 if price >= best_ask else -1 if price <= best_bid else 0


def trim(history, window):
    if len(history) > window:
        del history[: len(history) - window]


def update_anchor(scratch, mid):
    n = scratch.get("anchor_n", 0) + 1
    s = scratch.get("anchor_sum", 0.0) + mid
    scratch["anchor_n"] = n
    scratch["anchor_sum"] = s
    return s / n


def adjust_fair_for_aggressor_flow(cfg, fair, best_bid, best_ask, state, scratch):
    lam = cfg.get("aggressor_lambda", 0.0)
    if lam == 0.0:
        return fair
    flow = sum(
        aggressor_side(t.price, best_bid, best_ask) * t.quantity
        for t in state.market_trades.get(cfg["product"], [])
    )
    history = scratch.setdefault("agg_flow", [])
    history.append(flow)
    trim(history, AGGRESSOR_WINDOW)
    return fair + lam * sum(history)


def vol_widened_spread(cfg, scratch, best_bid, best_ask):
    mids = scratch.setdefault("mids", [])
    mids.append((best_bid + best_ask) / 2)
    trim(mids, VOL_WINDOW)
    if len(mids) < VOL_WINDOW // 2:
        return SPREAD_FRACTION
    baseline = cfg.get("baseline_vol", 1.5)
    vol = realized_vol(mids)
    if vol <= baseline or baseline <= 0:
        return SPREAD_FRACTION
    return min(1.0, SPREAD_FRACTION * min(VOL_SCALE_MAX, vol / baseline))


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


def make_quote(cfg, fair, best_bid, best_ask, position, c, bought, sold):
    product, limit = cfg["product"], cfg["position_limit"]
    qsize = cfg.get("quote_size", 20)
    skew = position * SKEW_PER_UNIT
    bid_px = min(math.floor(fair - c * (fair - best_bid) - skew), best_ask - 1)
    ask_px = max(math.ceil(fair + c * (best_ask - fair) - skew), best_bid + 1)
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
    fair = adjust_fair_for_aggressor_flow(cfg, fair, best_bid, best_ask, state, scratch)

    anchor = update_anchor(scratch, mid)
    c = vol_widened_spread(cfg, scratch, best_bid, best_ask)
    position = state.position.get(cfg["product"], 0)

    diverge, d_bought, d_sold = divergence_take_orders(
        cfg, depth, scratch, position, anchor, mid
    )
    pos_eff = position + d_bought - d_sold
    takes, bought, sold = take_orders(cfg, depth, fair, pos_eff)
    bought += d_bought
    sold += d_sold
    quotes = make_quote(cfg, fair, best_bid, best_ask, position, c, bought, sold)
    return diverge + takes + quotes


# =========================================================================
# Kalman-MR pipeline (delta-1 products: HYDROGEL_PACK, VELVETFRUIT_EXTRACT)
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

    k_ss = cfg["k_ss"]
    fair = scratch.get("_f", micro)
    innov = micro - fair
    err_ema = scratch.get("_err", abs(innov))
    err_ema += k_ss * (abs(innov) - err_ema)
    scratch["_err"] = err_ema
    fair += (k_ss / (1.0 + err_ema)) * innov
    scratch["_f"] = fair

    n = scratch.get("_n", 0) + 1
    s2 = scratch.get("_s2", 0.0) + (mid - fair) ** 2
    scratch["_n"], scratch["_s2"] = n, s2
    sigma = max(1.0, (s2 / n) ** 0.5) if n > 50 else cfg["sigma_init"]

    fair_static = cfg["fair_static"]
    anchor_alpha = cfg.get("anchor_alpha", 0.0)
    if anchor_alpha > 0:
        anchor = scratch.get("_anc", fair_static)
        anchor += anchor_alpha * (mid - anchor)
        scratch["_anc"] = anchor
    else:
        anchor = fair_static

    mr_gain = cfg["mr_gain"]
    target_short = mr_gain * (fair - mid) / sigma
    target_static = mr_gain * (anchor - fair) / sigma
    target = max(-limit, min(limit, round(target_short + target_static)))

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
        "fair_static": 10030,       # mean+40 — sweep optimum (offset captures spread asymmetry)
        "anchor_alpha": 0.0,        # day-mean rock stable → no drift adjustment
        "mr_gain": 2000,
        "sigma_init": 30.0,
        "take_max_pay": -6,         # only cross when offer ≥6 ticks below fair (~spread/2-2)
        "quote_edge": 3,            # wide spread (16) + thin TOB (25) → keep clear
        "quote_size": 30,
    },
    {
        "product": "VELVETFRUIT_EXTRACT",
        "position_limit": 200,
        "k_ss": 0.02,
        "fair_static": 5275,        # mean+25 — joint mean/min sweep optimum (re-sweep before submit)
        "anchor_alpha": 0.0,        # day-mean drift small → static beats EMA decisively
        "mr_gain": 2000,
        "sigma_init": 15.0,
        "take_max_pay": -2,         # only cross when offer ≥2 ticks below fair (~spread/2-1)
        "quote_edge": 1,            # narrow spread (5) + deep TOB (75) → quote inside
        "quote_size": 30,
    },
]

ZSCORE_PRODUCTS = [
    {"product": "VEV_4000", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5,
     "aggressor_lambda": 0.015, "diverge_threshold": 25, "max_diverge_position": 295},
    {"product": "VEV_4500", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5,
     "diverge_threshold": 25, "max_diverge_position": 295},
    {"product": "VEV_5000", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5,
     "diverge_threshold": 22, "max_diverge_position": 295},
    {"product": "VEV_5100", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5,
     "diverge_threshold": 18, "max_diverge_position": 295},
    {"product": "VEV_5200", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5,
     "diverge_threshold": 14, "max_diverge_position": 295},
    {"product": "VEV_5300", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5,
     "diverge_threshold": 10, "max_diverge_position": 295},
    {"product": "VEV_5400", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5,
     "diverge_threshold": 5, "max_diverge_position": 295},
    {"product": "VEV_5500", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.3,
     "diverge_threshold": 3, "max_diverge_position": 295},
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

        for cfg in ZSCORE_PRODUCTS:
            ors = zscore_orders(cfg, state, store.setdefault(cfg["product"], {}))
            if ors:
                orders[cfg["product"]] = ors

        return orders, 0, json.dumps(store)
