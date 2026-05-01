"""Round 3 v20 — seven structural improvements over v16.

User-prescribed changes (each is structural, not a parameter sweep):
  1. HP fair_static 10030 -> 9990 (the actual 3-day mean — eliminates the
     structural long bias that bled through Day-0/Day-2 down-trends).
  2. tanh position-targeting in place of clamped-linear:
        target = round(limit * tanh(mr_gain * (anchor - mid) / (sigma * limit)))
     Smooth S-curve, never sits at the rail except in extremes.
  3. Inventory-aware quote-size skew (skew = position / SKEW_UNIT) — when
     long, post smaller bid + bigger ask so inventory drains organically.
  4. Inventory-aware take width — tighter take when |position|/limit is high
     (don't pile on more risk when the book is already full).
  5. VEV max_diverge_position 295 -> 150 — keeps half the limit available
     for two-sided making after a single divergence event.
  6. VEV anchor uses EMA (alpha=0.005, ~200-tick window) instead of cumulative
     mean — recent prices matter more for divergence detection.
  7. Drawdown circuit-breaker — track running cash-flow PnL in traderData;
     when realised drawdown exceeds DRAWDOWN_THRESHOLD, halve quote sizes
     and disable divergence-takes for COOLDOWN_TICKS.

Plus the v12 informed-flow signal (size>=11 VFE prints) is kept verbatim.

KEY FINDING: at limit=200/300 (the IMC actual limits), hybrid.py historical
PnL = 716,008. v01-v10 all add layers (Stoikov skew, dual-anchor MR, IV
scalp, exit-at-fair, EMA trend) on top of hybrid's Kalman-MR + tighter VEV
divergence — and every layer DETRACTS at full limits (v10 = 243k vs hybrid
716k = -473k regression). The simpler the better.

v11 adds the SINGLE empirically-validated signal: VFE size>=11 informed-trade
prints predict +4.5 mid move over 500 ticks (49 events / 3 days). Bias the
Kalman target by INFORMED_GAIN_S * decayed_signal. Two new constants only.

If informed-flow doesn't help at MC, fall back to vanilla hybrid.

Round 3 submission — two pipelines, one Trader.

  HYDROGEL_PACK / VELVETFRUIT_EXTRACT → Kalman-MR (proportional reversion to fair_static)
  VEV_4000 … VEV_5500                 → anchor-divergence + market-make

PRE-SUBMISSION CHECKLIST:
  - Re-sweep fair_static for HYDROGEL/VELVETFRUIT against the latest day CSVs.
    Score by per-day mean AND min PnL — IMC submissions run a single day,
    so a high-mean param that bombs on one day is worse than a flatter one.
  - Validate against the latest Prosperity sandbox log with --max-timestamp
    matching sandbox length; per-product PnL should match within ~5%.
"""

import json
import math

from datamodel import Order, TradingState

TAKE_WIDTH = 1
ANCHOR_WARMUP = 100
DIVERGE_TAKE_SIZE = 30

# v11: VFE informed-flow signal (only addition vs hybrid.py).
INFORMED_SIZE_VFE = 11
INFORMED_GAIN_S = 10
INFORMED_DECAY = 0.998

# v20 structural additions
SKEW_UNIT = 20            # 1 unit of quote-size skew per 20 lots of inventory
TAKE_TIGHTEN_FACTOR = 4   # additional ticks of tightening at full inventory
VEV_ANCHOR_ALPHA = 0.005  # EMA decay for VEV anchor (~200-tick window)
DRAWDOWN_THRESHOLD = -15_000  # circuit-breaker triggers at this realised PnL drop
COOLDOWN_TICKS = 5_000    # tick-window where reduced exposure persists
TICK_STEP = 100           # state.timestamp increments per tick


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


def divergence_take_orders(cfg, depth, scratch, position, anchor, mid, cb_active=False):
    threshold = cfg.get("diverge_threshold", 0)
    # v20 change #7: drawdown circuit-breaker disables divergence takes.
    if cb_active:
        return [], 0, 0
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


def make_quote(cfg, fair, best_bid, best_ask, position, bought, sold, cb_active=False):
    product, limit = cfg["product"], cfg["position_limit"]
    qsize = cfg.get("quote_size", 20)
    # v20 change #7: halve quote size during circuit-breaker cooldown.
    if cb_active:
        qsize = max(1, qsize // 2)
    # v20 change #3: inventory-aware skew on VEV quotes too.
    skew = round(position / SKEW_UNIT)
    bid_px = min(math.floor((fair + best_bid) / 2), best_ask - 1)
    ask_px = max(math.ceil((fair + best_ask) / 2), best_bid + 1)
    buy = max(0, min(qsize, limit - position - bought) - max(0, skew))
    sell = max(0, min(qsize, limit + position - sold) - max(0, -skew))
    out = []
    if buy > 0 and bid_px < ask_px:
        out.append(Order(product, bid_px, buy))
    if sell > 0 and ask_px > bid_px:
        out.append(Order(product, ask_px, -sell))
    return out


def zscore_orders(cfg, state, scratch, cb_active=False):
    depth = state.order_depths.get(cfg["product"])
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []

    best_bid = max(depth.buy_orders)
    best_ask = min(depth.sell_orders)
    mid = (best_bid + best_ask) / 2
    fair = full_depth_mid(depth)

    # v20 change #6: VEV anchor uses EMA (alpha=VEV_ANCHOR_ALPHA, ~200-tick
    # window) instead of cumulative mean — recent prices matter more than
    # noisy first-100 ticks.
    n = scratch.get("anchor_n", 0) + 1
    scratch["anchor_n"] = n
    anchor = scratch.get("_ema_anchor", mid)
    anchor = anchor + VEV_ANCHOR_ALPHA * (mid - anchor)
    scratch["_ema_anchor"] = anchor
    position = state.position.get(cfg["product"], 0)

    diverge, d_bought, d_sold = divergence_take_orders(
        cfg, depth, scratch, position, anchor, mid, cb_active=cb_active,
    )
    pos_eff = position + d_bought - d_sold
    takes, bought, sold = take_orders(cfg, depth, fair, pos_eff)
    bought += d_bought
    sold += d_sold
    quotes = make_quote(cfg, fair, best_bid, best_ask, position, bought, sold,
                        cb_active=cb_active)
    return diverge + takes + quotes


# =========================================================================
# Kalman-MR pipeline (HYDROGEL_PACK, VELVETFRUIT_EXTRACT)
# =========================================================================


def kalman_mr_orders(cfg, depth, position, scratch, target_bias=0, cb_active=False):
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

    # v20 change #2: TANH targeting in place of clamped-linear. Smooth
    # S-curve never sits at the rail except in extremes; decays smoothly
    # back to zero on reversion.
    anchor = cfg["fair_static"]
    raw_target = cfg["mr_gain"] * (anchor - mid) / sigma
    target = round(limit * math.tanh(raw_target / limit)) + target_bias
    target = max(-limit, min(limit, target))

    # v20 change #4: tighten take_max_pay as inventory fills up — don't add
    # more risk when the book is already loaded on this side.
    inventory_use = abs(position) / limit if limit > 0 else 0.0
    take_max_pay = cfg["take_max_pay"] - TAKE_TIGHTEN_FACTOR * inventory_use

    quote_edge = cfg["quote_edge"]
    quote_size = cfg["quote_size"]
    if cb_active:
        quote_size = max(1, quote_size // 2)  # v20 #7: halve during cooldown

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

    # v20 change #3: inventory-aware quote-size skew. When long, post smaller
    # bid + bigger ask so inventory drains naturally.
    skew = round(position / SKEW_UNIT)
    baaf = min((p for p in depth.sell_orders if p >= fair + quote_edge), default=None)
    bbbf = max((p for p in depth.buy_orders if p <= fair - quote_edge), default=None)
    if bbbf is not None:
        buy_q = max(0, min(quote_size, limit - position - bv) - max(0, skew))
        if buy_q > 0:
            orders.append(Order(product, bbbf + 1, buy_q))
    if baaf is not None:
        sell_q = max(0, min(quote_size, limit + position - sv) - max(0, -skew))
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
        # v20 change #1: fair_static 10030 -> 9990 (actual 3-day mean,
        # eliminates structural long bias in down-trending sessions).
        "fair_static": 9990,
        "mr_gain": 1000,
        "sigma_init": 30.0,
        "take_max_pay": -6,
        "quote_edge": 3,
        "quote_size": 30,
    },
    {
        "product": "VELVETFRUIT_EXTRACT",
        "position_limit": 200,
        "k_ss": 0.02,
        # VFE actual 3-day mean = 5250; fair_static was 5275 (+25 bias).
        # Apply same correction as HP.
        "fair_static": 5250,
        "mr_gain": 2000,
        "sigma_init": 15.0,
        "take_max_pay": -2,
        "quote_edge": 1,
        "quote_size": 30,
    },
]

ZSCORE_PRODUCTS = [
    {"product": "VEV_4000", "position_limit": 300, "quote_size": 30, "diverge_threshold": 18, "max_diverge_position": 150},
    {"product": "VEV_4500", "position_limit": 300, "quote_size": 30, "diverge_threshold": 18, "max_diverge_position": 150},
    {"product": "VEV_5000", "position_limit": 300, "quote_size": 30, "diverge_threshold": 15, "max_diverge_position": 150},
    {"product": "VEV_5100", "position_limit": 300, "quote_size": 30, "diverge_threshold": 13, "max_diverge_position": 150},
    {"product": "VEV_5200", "position_limit": 300, "quote_size": 30, "diverge_threshold": 10, "max_diverge_position": 150},
    {"product": "VEV_5300", "position_limit": 300, "quote_size": 30, "diverge_threshold": 7, "max_diverge_position": 150},
    {"product": "VEV_5400", "position_limit": 300, "quote_size": 30, "diverge_threshold": 4, "max_diverge_position": 150},
    {"product": "VEV_5500", "position_limit": 300, "quote_size": 30, "diverge_threshold": 2, "max_diverge_position": 150},
]


def update_informed_signal(store, market_trades_vfe, vfe_bid, vfe_ask):
    """v11: VFE size>=11 informed-flow signal (decayed signed-volume EMA)."""
    sig = store.get("_inf", 0.0) * INFORMED_DECAY
    for t in market_trades_vfe or []:
        if t.quantity < INFORMED_SIZE_VFE:
            continue
        if t.price >= vfe_ask:
            sig += t.quantity
        elif t.price <= vfe_bid:
            sig -= t.quantity
    store["_inf"] = sig
    return sig


def update_circuit_breaker(store, state):
    """v20 change #7: track cash-flow PnL from own_trades; trip when realised
    drawdown exceeds DRAWDOWN_THRESHOLD; cooldown for COOLDOWN_TICKS."""
    cash = store.get("_cash", 0.0)
    peak = store.get("_peak", 0.0)
    for arr in state.own_trades.values():
        for t in arr:
            # SUBMISSION buyer => we bought (cash decreases), seller => we sold.
            if t.buyer == "SUBMISSION":
                cash -= t.price * t.quantity
            elif t.seller == "SUBMISSION":
                cash += t.price * t.quantity
    if cash > peak:
        peak = cash
    store["_cash"], store["_peak"] = cash, peak
    drawdown = cash - peak
    cooldown_until = store.get("_cd_until", 0)
    if drawdown < DRAWDOWN_THRESHOLD:
        cooldown_until = max(cooldown_until,
                             state.timestamp // TICK_STEP + COOLDOWN_TICKS)
        store["_cd_until"] = cooldown_until
    return state.timestamp // TICK_STEP < cooldown_until


class Trader:
    def bid(self):
        return 0

    def run(self, state: TradingState):
        try:
            store = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            store = {}

        orders: dict[str, list[Order]] = {}

        # v20 #7: drawdown circuit-breaker shared across all products.
        cb_active = update_circuit_breaker(store.setdefault("_cb", {}), state)

        for cfg in KALMAN_MR_PRODUCTS:
            depth = state.order_depths.get(cfg["product"])
            target_bias = 0
            # v11: VFE-only informed-flow target bias.
            if (cfg["product"] == "VELVETFRUIT_EXTRACT"
                    and depth and depth.buy_orders and depth.sell_orders):
                vfe_bid_ = max(depth.buy_orders)
                vfe_ask_ = min(depth.sell_orders)
                sig = update_informed_signal(
                    store.setdefault("_inf_store", {}),
                    state.market_trades.get("VELVETFRUIT_EXTRACT", []),
                    vfe_bid_, vfe_ask_,
                )
                target_bias = int(round(INFORMED_GAIN_S * sig))
            ors = kalman_mr_orders(cfg, depth, state.position.get(cfg["product"], 0),
                                   store.setdefault(cfg["product"], {}),
                                   target_bias=target_bias,
                                   cb_active=cb_active)
            if ors:
                orders[cfg["product"]] = ors

        for cfg in ZSCORE_PRODUCTS:
            ors = zscore_orders(cfg, state, store.setdefault(cfg["product"], {}),
                                cb_active=cb_active)
            if ors:
                orders[cfg["product"]] = ors

        return orders, 0, json.dumps(store)
