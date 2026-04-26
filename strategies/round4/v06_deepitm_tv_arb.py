"""
Round 4 v06 — v05 base + TV-arb overlay for deep-ITM (4000/4500).

v05 zeroed deep-ITM divergence PnL (-$49k vs v01) by switching to pure
delta-1 MM. Round-4 data shows |TV = mid - max(0, S-K)| exceeds 3 on
about 2% of ticks (max excursions ±7) — these are the moments where
the voucher genuinely mispriced vs spot, and where convergence pays.

v06 keeps v05's delta-1 fair price but adds a bounded TV-arb take:
when |TV| > tv_arb_threshold, lift offers below fair (TV<0) or sell
into bids above fair (TV>0), capped at max_arb_position to prevent the
kind of stuck-short that lost v01 $9k on D3.

Other pipelines unchanged from v05.
"""

import json
import math

from datamodel import Order, TradingState

# VEV expiry: round-3 day 0 had TTE=5d; round-4 day 1 inherits 4d.
# Each within-day tick = 1/10000 of a day.
TTE_AT_FIRST_DAY = 4.0


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S, K, T, sigma):
    if sigma <= 1e-9 or T <= 0:
        return max(S - K, 0.0)
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sq)
    return S * _norm_cdf(d1) - K * _norm_cdf(d1 - sigma * sq)


def implied_vol(price, S, K, T, lo=0.001, hi=2.5):
    if T <= 0 or price <= max(S - K, 0.0) + 1e-6:
        return None
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if bs_call(S, K, T, mid) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def smile_update(store, m, iv, decay=0.999):
    for k in ("n", "sm", "sm2", "sm3", "sm4", "siv", "smiv", "sm2iv"):
        store[k] = store.get(k, 0.0) * decay
    store["n"]     += 1.0
    store["sm"]    += m
    store["sm2"]   += m * m
    store["sm3"]   += m ** 3
    store["sm4"]   += m ** 4
    store["siv"]   += iv
    store["smiv"]  += m * iv
    store["sm2iv"] += m * m * iv


def smile_fit(store):
    """Solve [Σm⁴ Σm³ Σm²; Σm³ Σm² Σm; Σm² Σm n] · [a,b,c]ᵀ = [Σm²·iv, Σm·iv, Σiv]ᵀ
    via Cramer's rule. Returns (a, b, c) or None if N too small / singular."""
    n = store.get("n", 0.0)
    if n < 200:
        return None
    a11, a12, a13 = store["sm4"], store["sm3"], store["sm2"]
    a21, a22, a23 = store["sm3"], store["sm2"], store["sm"]
    a31, a32, a33 = store["sm2"], store["sm"], n
    b1, b2, b3 = store["sm2iv"], store["smiv"], store["siv"]
    det = (a11 * (a22 * a33 - a23 * a32)
           - a12 * (a21 * a33 - a23 * a31)
           + a13 * (a21 * a32 - a22 * a31))
    if abs(det) < 1e-12:
        return None
    da = (b1 * (a22 * a33 - a23 * a32)
          - a12 * (b2 * a33 - a23 * b3)
          + a13 * (b2 * a32 - a22 * b3))
    db = (a11 * (b2 * a33 - a23 * b3)
          - b1 * (a21 * a33 - a23 * a31)
          + a13 * (a21 * b3 - b2 * a31))
    dc = (a11 * (a22 * b3 - b2 * a32)
          - a12 * (a21 * b3 - b2 * a31)
          + b1 * (a21 * a32 - a22 * a31))
    return da / det, db / det, dc / det

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
# Smile-arb pipeline (active VEV strikes 5000-5500)
# =========================================================================


def smile_arb_take_orders(cfg, depth, mid, fair, position):
    """Take rich/cheap relative to BS-smile fair. resid = mid - fair."""
    threshold = cfg.get("smile_arb_threshold", 3.0)
    resid = mid - fair
    if abs(resid) < threshold:
        return [], 0, 0

    product, limit = cfg["product"], cfg["position_limit"]
    max_pos = cfg.get("max_arb_position", 295)
    take_size = cfg.get("arb_take_size", 30)
    out, bought, sold = [], 0, 0
    if resid > 0 and position > -max_pos:
        # Voucher trades rich vs smile fair → sell into bids
        room = position + max_pos
        for price, qty in search_buys(depth):
            if price < fair:
                break
            cap = min(limit + position - sold, take_size - sold, room - sold)
            if cap <= 0:
                break
            take = min(qty, cap)
            out.append(Order(product, price, -take))
            sold += take
    elif resid < 0 and position < max_pos:
        # Voucher trades cheap vs smile fair → lift offers
        room = max_pos - position
        for price, qty in search_sells(depth):
            if price > fair:
                break
            cap = min(limit - position - bought, take_size - bought, room - bought)
            if cap <= 0:
                break
            take = min(qty, cap)
            out.append(Order(product, price, take))
            bought += take
    return out, bought, sold


def smile_arb_orders(cfg, state, smile_pool, per_strike, S, T):
    """Active-strike pipeline: layer smile arb ON TOP OF the legacy
    divergence path. Smile residuals are small ($1-3) — they're a *bonus*
    signal, not a replacement for the directional-drift divergence layer."""
    depth = state.order_depths.get(cfg["product"])
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []

    K = cfg["strike"]
    best_bid = max(depth.buy_orders)
    best_ask = min(depth.sell_orders)
    mid = (best_bid + best_ask) / 2
    book_fair = full_depth_mid(depth)
    position = state.position.get(cfg["product"], 0)

    # Update smile pool with this tick's IV (and warm up the running anchor).
    if S > 0 and T > 0:
        iv = implied_vol(mid, S, K, T)
        if iv is not None:
            m = math.log(K / S) / math.sqrt(T)
            smile_update(smile_pool, m, iv)

    n = per_strike.get("anchor_n", 0) + 1
    s = per_strike.get("anchor_sum", 0.0) + mid
    per_strike["anchor_n"], per_strike["anchor_sum"] = n, s
    anchor = s / n

    # Smile-arb take first (small extra signal on top of divergence).
    coeffs = smile_fit(smile_pool)
    arb, a_bought, a_sold = [], 0, 0
    if coeffs is not None and T > 0 and S > 0:
        a, b, c = coeffs
        m_now = math.log(K / S) / math.sqrt(T)
        smile_iv = max(0.05, min(2.5, a * m_now * m_now + b * m_now + c))
        smile_fair = bs_call(S, K, T, smile_iv)
        arb, a_bought, a_sold = smile_arb_take_orders(
            cfg, depth, mid, smile_fair, position
        )

    # Then the legacy directional-drift divergence layer.
    pos_eff = position + a_bought - a_sold
    diverge, d_bought, d_sold = divergence_take_orders(
        cfg, depth, per_strike, pos_eff, anchor, mid
    )
    pos_eff += d_bought - d_sold

    takes, t_bought, t_sold = take_orders(cfg, depth, book_fair, pos_eff)
    bought = a_bought + d_bought + t_bought
    sold = a_sold + d_sold + t_sold

    quotes = make_quote(cfg, book_fair, best_bid, best_ask, position, bought, sold)
    return arb + diverge + takes + quotes


def compute_T(timestamp):
    """Days-to-expiry → years. Round-4 day 1 starts at TTE_AT_FIRST_DAY days."""
    day = timestamp // 1_000_000
    tick = (timestamp % 1_000_000) // 100
    days_passed = (day - 1) + tick / 10_000
    return (TTE_AT_FIRST_DAY - days_passed) / 365.0


# =========================================================================
# Delta-1 pipeline (deep-ITM VEV_4000, VEV_4500)
# =========================================================================


def delta1_orders(cfg, state, S):
    """fair := max(0, S - K). Three layers:

    1. TV-arb take: when |mid - fair| > tv_arb_threshold, take the
       convergence side, capped at ±max_arb_position to keep us from
       loading a stuck short like v01's D3 -$9k.
    2. Standard take: lift offers below fair / sell into bids above.
    3. Tight MM quotes around fair.
    """
    depth = state.order_depths.get(cfg["product"])
    if not depth or not depth.buy_orders or not depth.sell_orders or S <= 0:
        return []
    K = cfg["strike"]
    fair = max(0.0, S - K)
    position = state.position.get(cfg["product"], 0)
    limit = cfg["position_limit"]
    product = cfg["product"]
    best_bid = max(depth.buy_orders)
    best_ask = min(depth.sell_orders)
    mid = (best_bid + best_ask) / 2.0
    tv = mid - fair

    arb = []
    a_bought = a_sold = 0
    threshold = cfg.get("tv_arb_threshold", 3.0)
    max_arb_pos = cfg.get("max_arb_position", 60)
    arb_size = cfg.get("arb_take_size", 30)

    if tv > threshold and position > -max_arb_pos:
        # Voucher rich vs intrinsic → sell into bids above fair.
        room = position + max_arb_pos
        for price, qty in search_buys(depth):
            if price < fair:
                break
            cap = min(limit + position - a_sold, arb_size - a_sold, room - a_sold)
            if cap <= 0:
                break
            take = min(qty, cap)
            arb.append(Order(product, price, -take))
            a_sold += take
    elif tv < -threshold and position < max_arb_pos:
        # Voucher cheap vs intrinsic → lift offers below fair.
        room = max_arb_pos - position
        for price, qty in search_sells(depth):
            if price > fair:
                break
            cap = min(limit - position - a_bought, arb_size - a_bought, room - a_bought)
            if cap <= 0:
                break
            take = min(qty, cap)
            arb.append(Order(product, price, take))
            a_bought += take

    pos_eff = position + a_bought - a_sold
    takes, t_bought, t_sold = take_orders(cfg, depth, fair, pos_eff)
    bought = a_bought + t_bought
    sold = a_sold + t_sold
    quotes = make_quote(cfg, fair, best_bid, best_ask, position, bought, sold)
    return arb + takes + quotes


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
        "mr_gain": 1000,            # v02: halved (weak round-4 MR)
        "sigma_init": 15.0,
        "take_max_pay": -4,         # v02: raised from -2
        "quote_edge": 1,
        "quote_size": 15,           # v02: halved
    },
]

ZSCORE_PRODUCTS = [
    # No deep-ITM here in v05; they go through DELTA1_PRODUCTS.
]

DELTA1_PRODUCTS = [
    {"product": "VEV_4000", "strike": 4000, "position_limit": 300, "quote_size": 30,
     "tv_arb_threshold": 3.0, "max_arb_position": 60, "arb_take_size": 30},
    {"product": "VEV_4500", "strike": 4500, "position_limit": 300, "quote_size": 30,
     "tv_arb_threshold": 3.0, "max_arb_position": 60, "arb_take_size": 30},
]

SMILE_ARB_PRODUCTS = [
    # Active strikes — smile_arb_threshold ~2σ of per-strike residual sd
    # (1.5–5.3 from round-4 data). Layered ON TOP OF the legacy divergence
    # path, so diverge_threshold + max_diverge_position are kept from v01.
    {"product": "VEV_5000", "strike": 5000, "position_limit": 300, "quote_size": 30, "diverge_threshold": 18, "max_diverge_position": 295, "smile_arb_threshold": 3.0, "max_arb_position": 60, "arb_take_size": 15},
    {"product": "VEV_5100", "strike": 5100, "position_limit": 300, "quote_size": 30, "diverge_threshold": 14, "max_diverge_position": 295, "smile_arb_threshold": 5.0, "max_arb_position": 60, "arb_take_size": 15},
    {"product": "VEV_5200", "strike": 5200, "position_limit": 300, "quote_size": 30, "diverge_threshold": 11, "max_diverge_position": 295, "smile_arb_threshold": 7.0, "max_arb_position": 60, "arb_take_size": 15},
    {"product": "VEV_5300", "strike": 5300, "position_limit": 300, "quote_size": 30, "diverge_threshold":  8, "max_diverge_position": 295, "smile_arb_threshold": 7.0, "max_arb_position": 60, "arb_take_size": 15},
    {"product": "VEV_5400", "strike": 5400, "position_limit": 300, "quote_size": 30, "diverge_threshold":  4, "max_diverge_position": 295, "smile_arb_threshold": 5.0, "max_arb_position": 60, "arb_take_size": 15},
    {"product": "VEV_5500", "strike": 5500, "position_limit": 300, "quote_size": 30, "diverge_threshold":  2, "max_diverge_position": 295, "smile_arb_threshold": 3.0, "max_arb_position": 60, "arb_take_size": 15},
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

        # Spot for smile fit: VFRUIT mid this tick (or last cached).
        vf_depth = state.order_depths.get(VFRUIT)
        if vf_depth and vf_depth.buy_orders and vf_depth.sell_orders:
            S = (max(vf_depth.buy_orders) + min(vf_depth.sell_orders)) / 2.0
            store["_S_last"] = S
        else:
            S = store.get("_S_last", 0.0)
        T = compute_T(state.timestamp)
        smile_store = store.setdefault("_smile_pool", {})

        for cfg in DELTA1_PRODUCTS:
            ors = delta1_orders(cfg, state, S)
            if ors:
                orders[cfg["product"]] = ors

        for cfg in SMILE_ARB_PRODUCTS:
            ors = smile_arb_orders(cfg, state, smile_store,
                                   store.setdefault(cfg["product"], {}), S, T)
            if ors:
                orders[cfg["product"]] = ors

        for cfg in ZSCORE_PRODUCTS:
            ors = zscore_orders(cfg, state, store.setdefault(cfg["product"], {}))
            if ors:
                orders[cfg["product"]] = ors

        return orders, 0, json.dumps(store)
