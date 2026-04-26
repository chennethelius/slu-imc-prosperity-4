"""
Round 4 — direction-aware regime gate on top of asymm threshold.

Round-4 V-shape pattern (from round4_exploration.ipynb): D1+D2 trend
+48 ticks above long-term mean ($5248), then D3 violently reverses
(-63 in q1-q2), overshoots downside (trough $5191), then **q4 recovers
to $5247** — within $1 of long-term mean. Recovery is a near-
deterministic +37 ticks from the trough.

Prior asymm_thresh (retired) defended the q1-q2 fall (regime gate
scales position cap to 0.30) but stayed defensive into the q4
recovery — giving up upside.

Direction-aware regime: same defensive scaling on the way DOWN, but
BOOST position cap to 1.3 once spot starts recovering from a local
low. Detection: rolling 500-tick min tracker. "Recovery mode" = trough
was hit ≥50 ticks ago AND current S ≥3 ticks above the trough.

  |z| ≥ 1.5 + falling   → 0.30 scale (defensive)
  |z| ≥ 1.5 + recovering → 1.3 scale (aggressive — captures rebound)
  |z| in (0.5, 1.5)     → linear, direction-dependent slope

Plus the asymmetric add/reduce threshold from asymm_thresh (kept).

Result vs prior frontier (QP=1.0):
                       D1       D2       D3      mean      min   mean+min
  softer_vfruit     244,058  208,026   54,358  168,814   54,358    223,172
  vol_threshold     235,552  163,222   65,790  154,855   65,790    220,645
  asymm_thresh      189,125  135,018   83,493  135,879   83,493    219,372  ← retired
  recovery_regime   197,208  132,219   91,602  140,343   91,602    231,945  ← BEST mean+min

Strict Pareto improvement over asymm_thresh (+$4.5k mean, +$8.1k min,
+$12.6k mean+min) and FIRST strategy to beat softer_vfruit's mean+min
ceiling. The V-bottom recovery insight is real and exploitable.

Original asymm_thresh notes (kept since this builds on it):

When position is long, raise the threshold for ADDING more long but
keep normal threshold for SELLING (reducing). Inverse when short.

  add_threshold    = base × vol_factor × (1 + 2|p|/limit)
  reduce_threshold = base × vol_factor

Trade-direction classification:
  diverge > 0 (strategy SELLS): position > 0 → reducing → base
                                position ≤ 0 → adding short → raised
  diverge < 0 (strategy BUYS):  position < 0 → reducing → base
                                position ≥ 0 → adding long → raised

Asymmetry keeps reducing trades flowing while slowing accumulation.
Replaced position_throttle (which symmetrically throttled both sides
and gave up $9k of mean).

Result vs prior frontier (QP=1.0):
                       D1       D2       D3      mean      min   mean+min
  softer_vfruit     244,058  208,026   54,358  168,814   54,358    223,172
  vol_threshold     235,552  163,222   65,790  154,855   65,790    220,645
  position_throttle 170,139  133,240   76,969  126,782   76,969    203,751  ← retired
  asymm_thresh      189,125  135,018   83,493  135,879   83,493    219,372  ← BEST MIN

asymm_thresh dominates position_throttle on every metric:
  +$9,097 mean, +$6,524 min, +$15,621 mean+min.

Pick this when min-day defense is the priority.
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


def divergence_take_orders(cfg, depth, scratch, position, anchor, mid, regime_scale=1.0):
    base_threshold = cfg.get("diverge_threshold", 0)
    # Vol-adjusted: track running std of (mid - last_mid), scale threshold
    # by the multiplier of this std vs its long-run baseline.
    last_mid = scratch.get("_last_mid", mid)
    diff = mid - last_mid
    scratch["_last_mid"] = mid
    vol_n = scratch.get("vol_n", 0) + 1
    vol_s2 = scratch.get("vol_s2", 0.0) + diff * diff
    scratch["vol_n"] = vol_n
    scratch["vol_s2"] = vol_s2
    vol_factor = 1.0
    if vol_n > 100:
        cur_vol = math.sqrt(vol_s2 / vol_n)
        baseline = scratch.get("_vol_baseline")
        if baseline is None and vol_n > 500:
            scratch["_vol_baseline"] = cur_vol
            baseline = cur_vol
        if baseline is not None and baseline > 0.1:
            vol_factor = max(1.0, cur_vol / baseline)
    if base_threshold <= 0 or scratch.get("anchor_n", 0) < ANCHOR_WARMUP:
        return [], 0, 0
    diverge = mid - anchor
    if diverge == 0:
        return [], 0, 0

    # Asymmetric: ADD-side threshold raised by position-aware factor;
    # REDUCE-side stays at base.
    limit_p = cfg.get("position_limit", 1)
    add_factor = 1.0 + 2.0 * abs(position) / max(1, limit_p)
    add_threshold = base_threshold * vol_factor * add_factor
    reduce_threshold = base_threshold * vol_factor
    # diverge > 0 → strategy will SELL into bids → reduces long, adds short
    if diverge > 0:
        is_reducing = (position > 0)
    else:
        is_reducing = (position < 0)
    threshold = reduce_threshold if is_reducing else add_threshold
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

        # Direction-aware regime: extreme |z| triggers defensive 0.30 scale
        # if spot is FALLING, but aggressive 1.3 scale if spot is RECOVERING
        # from a local low (post-trough reversal — captures D3 q4 rebound).
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

            # Track rolling 500-tick min and the tick at which it was hit
            min_tracker = store.setdefault("_min_tracker", {"min": S, "ts": state.timestamp})
            if S < min_tracker["min"]:
                min_tracker["min"] = S
                min_tracker["ts"] = state.timestamp
            # Forget the min if it's older than 500 ticks
            ticks_since_min = (state.timestamp - min_tracker["ts"]) // 100
            if ticks_since_min > 500:
                # Reset to a fresh min from the recent buffer
                if len(spot_buf) >= 50:
                    fresh_min = min(spot_buf[-50:])
                    min_tracker["min"] = fresh_min
                    min_tracker["ts"] = state.timestamp
                    ticks_since_min = 0

            if len(spot_buf) >= 100:
                mu_w = sum(spot_buf) / len(spot_buf)
                var_w = sum((x - mu_w) ** 2 for x in spot_buf) / len(spot_buf)
                sd_w = math.sqrt(max(1e-6, var_w))
                if sd_w > 0.5:
                    z = abs(S - mu_w) / sd_w
                    # Recovery detection: rolling-min was hit ≥50 ticks ago
                    # AND current S is ≥3 ticks above the trough.
                    in_recovery = (ticks_since_min >= 50 and
                                   S >= min_tracker["min"] + 3.0)
                    if z >= 1.5:
                        if in_recovery:
                            regime_scale = 1.3  # aggressive: capture rebound
                        else:
                            regime_scale = 0.30  # defensive: still falling
                    elif z > 0.5:
                        if in_recovery:
                            # Mild bias upward in transition zone
                            regime_scale = 1.0 + 0.20 * (z - 0.5)
                        else:
                            regime_scale = 1.0 - 0.70 * (z - 0.5)

        for cfg in ZSCORE_PRODUCTS:
            ors = zscore_orders(cfg, state, store.setdefault(cfg["product"], {}),
                                regime_scale)
            if ors:
                orders[cfg["product"]] = ors

        return orders, 0, json.dumps(store)
