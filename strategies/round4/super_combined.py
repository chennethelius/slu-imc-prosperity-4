"""
Round 4 — super_combined: per-asset best across all round-4 strategies.

Per-asset PnL audit (mean+min, QP=1.0, 3 days) over 43 round-4 strategies
identified the best strategy per asset. Two underlying pipelines power the
result, each owning a disjoint subset of the products:

  Conviction MR (HP, VFE, VEV_5000)
    - HYDROGEL_PACK         from v41_mm_conviction_boost
    - VELVETFRUIT_EXTRACT   from v41_mm_conviction_boost
    - VEV_5000              from v52_full_cap_high_conv (chain extension)

  Anchor divergence (VEV_*)
    - VEV_4000, 4500, 5400  from v01_named_informed_mm (no gating)
    - VEV_5100, 5300, 5500  from combined (defensive regime + vol adjust)
    - VEV_5200              from combined (peak ATM — no gating, full cap)

Backtest (QP=1.0):
                       D1       D2       D3      mean      min   mean+min
  super_combined    241,402  151,417  176,210  189,676  151,417    341,093

IMC sandbox 500163 = D3 first 1000 ticks: real PnL $2,504. Per-asset
matches BT-D3-1k within $100/asset across all products — backtester
calibrated correctly.

Pipeline 1 — Conviction MR
  Composite of three strengths in [0,1]:
    z_str    = clipped (|mid - fair| / stdev), trapezoidal in [z_min, z_max]
    ema_str  = sign-aligned (ema_fast - ema_slow) magnitude when fast/slow/
               very_slow agree on the dev direction; else 0
    inf_str  = signed sum of named-counterparty trades over recent window,
               only when its sign matches the dev direction
    conv     = w_z*z + w_ema*e + w_inf*i  (when z_str > 0)
  Drives:
    - Primary take volume = take_max * conv (cross up to fair +/- take_offset)
    - Position cap soft_cap_pct linear from base_cap to full_cap (with conv)
    - MM-leg quote size scaled by (1 + MM_BOOST*conv) on alpha-aligned side
    - For VFE: any unfilled take volume routes to VEV_5000 chain extension
      (buy/sell at intrinsic +/- offset, dynamic soft_cap with conv)

Pipeline 2 — Anchor divergence
  Per voucher: rolling-mean anchor of mid; trade when mid diverges past
  per-strike threshold. Per-config flags:
    regime_mode:  "off" | "defensive" (200-tick spot z gates position cap)
    use_vol_adjust: scale threshold by max(1, current_vol / baseline_vol)
"""

import json
import math
from datamodel import Order, TradingState


# ============================================================================
# Book walker — every "place orders walking one side" path uses this
# ============================================================================

def _walk_book(depth, side, sym, ok, qty_target):
    """Walk asks (side=+1) or bids (side=-1), placing orders until ok(price)
    becomes false or qty_target qty is filled.

    Bake every cap into qty_target up front: any cap of the form K - running
    can be expressed as `qty_target - filled` where qty_target is K_initial.
    Returns (orders_list, total_filled).
    """
    if side > 0:
        prices = sorted(depth.sell_orders)
        book = depth.sell_orders
    else:
        prices = sorted(depth.buy_orders, reverse=True)
        book = depth.buy_orders
    out, filled = [], 0
    for px in prices:
        if filled >= qty_target or not ok(px):
            break
        qty = min(abs(book[px]), qty_target - filled)
        if qty <= 0:
            break
        out.append(Order(sym, px, side * qty))
        filled += qty
    return out, filled


def _full_depth_mid(depth):
    bids = list(depth.buy_orders.items())
    asks = [(p, -v) for p, v in depth.sell_orders.items()]
    bv = sum(v for _, v in bids)
    av = sum(v for _, v in asks)
    if bv <= 0 or av <= 0:
        return (max(depth.buy_orders) + min(depth.sell_orders)) / 2
    return (sum(p * v for p, v in bids) / bv + sum(p * v for p, v in asks) / av) / 2


# ============================================================================
# Pipeline 1 — Conviction MR (HP, VFE, VFE -> VEV_5000 chain)
# ============================================================================

HP_CFG = {
    "prefix": "hp", "symbol": "HYDROGEL_PACK", "limit": 200, "fair": 10002,
    "stdev_init": 33.0, "var_alpha": 0.005,
    "qsize": 35, "flat_pull": 1.0, "mr_thresh": 4, "mr_boost": 1.5,
    "z_min": 0.7, "z_max": 2.0,
    "ema_fast": 0.30, "ema_slow": 0.05, "ema_vslow": 0.02, "ema_full": 1.5,
    "informed_lookback": 8, "informed_full": 30,
    "w_z": 0.50, "w_ema": 0.30, "w_inf": 0.20,
    "take_max": 80, "take_offset": 4,
    "base_cap_pct": 0.50, "full_cap_pct": 1.00, "hard_cap_pct": 0.95,
    "mark_weights": {"Mark 14": +1.5, "Mark 38": -1.0},
}

VFE_CFG = {
    "prefix": "vfe", "symbol": "VELVETFRUIT_EXTRACT", "limit": 200, "fair": 5249,
    "stdev_init": 17.0, "var_alpha": 0.005,
    "qsize": 30, "flat_pull": 1.0, "mr_thresh": 3, "mr_boost": 1.5,
    "z_min": 0.7, "z_max": 2.0,
    "ema_fast": 0.30, "ema_slow": 0.05, "ema_vslow": 0.02, "ema_full": 0.8,
    "informed_lookback": 10, "informed_full": 40,
    "w_z": 0.50, "w_ema": 0.30, "w_inf": 0.20,
    "take_max": 70, "take_offset": 3,
    "base_cap_pct": 0.50, "full_cap_pct": 1.00, "hard_cap_pct": 0.95,
    "mark_weights": {
        "Mark 67": +1.5, "Mark 49": -1.5, "Mark 22": -1.0,
        "Mark 14": -0.5, "Mark 55": +0.5,
    },
}

# VFE primary-take overflow routes here. v52 originally chained 4000 -> 4500
# -> 5000 -> 5100 -> 5200; we own only VEV_5000 here so the chain has one
# target — the other ITM strikes are filled by the anchor pipeline.
VFE_DELTA_VEV_5000 = {
    "symbol": "VEV_5000", "strike": 5000, "limit": 300,
    "soft_cap": 200, "soft_cap_max": 250, "offset": 8,
}

MM_BOOST = 1.0  # alpha-aligned MM quote size multiplier at conv=1.0


def _conviction(depth, td, market_trades, cfg):
    """z + EMA-trend + counterparty-flow -> composite conviction in [0, 1]."""
    if not depth.buy_orders or not depth.sell_orders:
        return None
    bb = max(depth.buy_orders)
    ba = min(depth.sell_orders)
    mid = (bb + ba) / 2.0
    p, fair = cfg["prefix"], cfg["fair"]

    # z-score deviation magnitude in [0, 1]
    dev = mid - fair
    var = ((1.0 - cfg["var_alpha"]) * td.get(f"_{p}_var", cfg["stdev_init"] ** 2)
           + cfg["var_alpha"] * dev * dev)
    td[f"_{p}_var"] = var
    stdev = max(cfg["stdev_init"] * 0.15, var ** 0.5)
    z = abs(dev / stdev)
    z_str = (0.0 if z < cfg["z_min"]
             else min(1.0, (z - cfg["z_min"]) / (cfg["z_max"] - cfg["z_min"])))
    direction = +1 if dev < 0 else -1

    # EMA-trend confirmation: counts only when fast/slow/very-slow all align
    ema_f = cfg["ema_fast"] * mid + (1 - cfg["ema_fast"]) * td.get(f"_{p}_ef", mid)
    ema_s = cfg["ema_slow"] * mid + (1 - cfg["ema_slow"]) * td.get(f"_{p}_es", mid)
    ema_vs = cfg["ema_vslow"] * mid + (1 - cfg["ema_vslow"]) * td.get(f"_{p}_evs", mid)
    td[f"_{p}_ef"], td[f"_{p}_es"], td[f"_{p}_evs"] = ema_f, ema_s, ema_vs
    short = ema_f - ema_s
    medium = ema_s - ema_vs
    s_sign = (short > 0) - (short < 0)
    m_sign = (medium > 0) - (medium < 0)
    ema_str = (min(1.0, abs(short) / cfg["ema_full"])
               if s_sign != 0 and s_sign == m_sign == direction else 0.0)

    # Named-counterparty net flow
    net = 0.0
    if market_trades:
        w = cfg["mark_weights"]
        for t in market_trades[-cfg["informed_lookback"]:]:
            q = int(t.quantity)
            net += w.get(t.buyer or "", 0.0) * q - w.get(t.seller or "", 0.0) * q
    inf_sign = (net > 0) - (net < 0)
    inf_str = (min(1.0, abs(net) / cfg["informed_full"])
               if inf_sign != 0 and inf_sign == direction else 0.0)

    conv = (cfg["w_z"] * z_str + cfg["w_ema"] * ema_str + cfg["w_inf"] * inf_str
            if z_str > 0 else 0.0)
    return bb, ba, mid, direction, conv


def _conviction_orders(state, cfg, all_orders, td):
    """Conviction MR for a primary product (HP or VFE).

    1. Hard-cap unwind toward fair if |pos| breaches hard_cap_pct * limit.
    2. Conviction-scaled primary take up to fair +/- take_offset.
    3. (VFE only) Route any unfilled primary-take overflow into VEV_5000.
    4. Always-on MM leg with conviction boost on alpha-aligned side.
    """
    sym = cfg["symbol"]
    depth = state.order_depths.get(sym)
    if depth is None:
        return []
    sig = _conviction(depth, td, state.market_trades.get(sym, []), cfg)
    if sig is None:
        return []
    bb, ba, mid, direction, conv = sig
    pos = state.position.get(sym, 0)
    limit, fair = cfg["limit"], cfg["fair"]
    hard_cap = cfg["hard_cap_pct"] * limit

    out, bv, sv = [], 0, 0

    # 1. Hard-cap unwind — sell if too long, buy if too short
    if pos > hard_cap:
        target = pos - int(hard_cap * 0.5)
        unw, filled = _walk_book(depth, -1, sym, lambda px: px >= fair - 2, target)
        out.extend(unw); sv += filled
    elif pos < -hard_cap:
        target = -pos - int(hard_cap * 0.5)
        unw, filled = _walk_book(depth, +1, sym, lambda px: px <= fair + 2, target)
        out.extend(unw); bv += filled

    # 2. Conviction-scaled primary take
    primary_target = int(round(cfg["take_max"] * conv)) if conv > 0 else 0
    primary_taken = 0
    if conv > 0 and direction != 0:
        soft_cap = (cfg["base_cap_pct"]
                    + (cfg["full_cap_pct"] - cfg["base_cap_pct"]) * conv) * limit
        pos_after = pos + bv - sv
        offset = cfg["take_offset"]
        if direction > 0 and pos_after < soft_cap:
            qty_target = min(primary_target, limit - pos - bv, int(soft_cap - pos_after))
            takes, filled = _walk_book(
                depth, +1, sym, lambda px: px <= fair + offset, qty_target,
            )
            out.extend(takes); bv += filled; primary_taken = filled
        elif direction < 0 and pos_after > -soft_cap:
            qty_target = min(primary_target, limit + pos - sv, int(soft_cap + pos_after))
            takes, filled = _walk_book(
                depth, -1, sym, lambda px: px >= fair - offset, qty_target,
            )
            out.extend(takes); sv += filled; primary_taken = filled

    # 3. VFE -> VEV_5000 chain extension (overflow routing)
    overflow = primary_target - primary_taken
    if cfg["prefix"] == "vfe" and conv > 0 and overflow > 0 and direction != 0:
        v = VFE_DELTA_VEV_5000
        v_depth = state.order_depths.get(v["symbol"])
        if v_depth and v_depth.buy_orders and v_depth.sell_orders:
            v_pos = state.position.get(v["symbol"], 0)
            intrinsic = int(round(mid)) - v["strike"]
            v_soft = int(round(v["soft_cap"]
                               + (v["soft_cap_max"] - v["soft_cap"]) * conv))
            v_offset = v["offset"]
            v_orders = []
            if direction > 0 and v_pos < v_soft:
                qty_target = min(overflow, v["limit"] - v_pos, v_soft - v_pos)
                v_orders, _ = _walk_book(
                    v_depth, +1, v["symbol"],
                    lambda px: px <= intrinsic + v_offset, qty_target,
                )
            elif direction < 0 and v_pos > -v_soft:
                qty_target = min(overflow, v["limit"] + v_pos, v_soft + v_pos)
                v_orders, _ = _walk_book(
                    v_depth, -1, v["symbol"],
                    lambda px: px >= intrinsic - v_offset, qty_target,
                )
            if v_orders:
                all_orders[v["symbol"]] = v_orders

    # 4. MM leg — conviction boost on alpha-aligned side
    pos_after = pos + bv - sv
    mr_dir = (+1 if mid < fair - cfg["mr_thresh"]
              else -1 if mid > fair + cfg["mr_thresh"] else 0)
    bid_px = min(bb + 1, fair - 1)
    ask_px = max(ba - 1, fair + 1)
    ratio = pos_after / limit
    bm = max(0.0, 1.0 - cfg["flat_pull"] * ratio)
    sm = max(0.0, 1.0 + cfg["flat_pull"] * ratio)
    if mr_dir > 0:
        bm *= cfg["mr_boost"]
    elif mr_dir < 0:
        sm *= cfg["mr_boost"]
    if conv > 0:
        if direction > 0:
            bm *= 1.0 + MM_BOOST * conv
        elif direction < 0:
            sm *= 1.0 + MM_BOOST * conv
    bq = max(0, min(int(round(cfg["qsize"] * bm)), limit - pos - bv))
    sq = max(0, min(int(round(cfg["qsize"] * sm)), limit + pos - sv))
    if bid_px < ask_px:
        if bq > 0:
            out.append(Order(sym, int(bid_px), bq))
        if sq > 0:
            out.append(Order(sym, int(ask_px), -sq))
    return out


def _vev_5000_flatten(state):
    """Passive close-out for inherited VEV_5000 inventory at touch +/- 1."""
    vfe_depth = state.order_depths.get("VELVETFRUIT_EXTRACT")
    if not vfe_depth or not vfe_depth.buy_orders or not vfe_depth.sell_orders:
        return None
    v = VFE_DELTA_VEV_5000
    v_pos = state.position.get(v["symbol"], 0)
    if v_pos == 0:
        return None
    v_depth = state.order_depths.get(v["symbol"])
    if not v_depth or not v_depth.buy_orders or not v_depth.sell_orders:
        return None
    vfe_mid = int(round((max(vfe_depth.buy_orders) + min(vfe_depth.sell_orders)) / 2.0))
    intrinsic = vfe_mid - v["strike"]
    if v_pos > 0:
        px = max(intrinsic + 1, min(v_depth.sell_orders) - 1)
        qty = min(v_pos, 50, v["limit"] + v_pos)
        return Order(v["symbol"], px, -qty) if qty > 0 else None
    px = min(intrinsic - 1, max(v_depth.buy_orders) + 1)
    qty = min(-v_pos, 50, v["limit"] - v_pos)
    return Order(v["symbol"], px, qty) if qty > 0 else None


# ============================================================================
# Pipeline 2 — Anchor divergence (VEV_4000, 4500, 5100, 5200, 5300, 5400, 5500)
# ============================================================================

TAKE_WIDTH = 1
ANCHOR_WARMUP = 500  # raised from 100 — wait for anchor to stabilize before
                     # trading. Cumulative-mean anchor lags directional moves
                     # in early ticks; 100-tick warmup let the anchor pipeline
                     # accumulate wrong-way positions during D3's down-leg.
                     # Sweep at 200/500/1000/1500 found 500 optimal: +$7k full
                     # mean+min, +$5k 1k mean+min, no D2/min sacrifice.
DIVERGE_TAKE_SIZE = 30
MAX_DIVERGE_POS = 295  # near hard limit; gated by regime_scale when defensive

# Per-asset config (audit winners):
#   "off"       — v01 mode: no spot-z gating, base threshold
#   "defensive" — cmb mode: spot-z gates position cap down at extreme |z|
#   use_vol_adjust scales threshold by max(1, current_mid_vol / baseline)
ANCHOR_VEV_CFGS = [
    # ITM strikes — v01 wins (no gating, higher base threshold)
    {"product": "VEV_4000", "limit": 300, "qsize": 30, "diverge_threshold": 25,
     "regime_mode": "off",       "use_vol_adjust": False},
    {"product": "VEV_4500", "limit": 300, "qsize": 30, "diverge_threshold": 25,
     "regime_mode": "off",       "use_vol_adjust": False},
    {"product": "VEV_5400", "limit": 300, "qsize": 30, "diverge_threshold": 5,
     "regime_mode": "off",       "use_vol_adjust": False},
    # OTM strikes — combined wins (defensive z-gate + vol adjust)
    {"product": "VEV_5100", "limit": 300, "qsize": 30, "diverge_threshold": 14,
     "regime_mode": "defensive", "use_vol_adjust": True},
    {"product": "VEV_5300", "limit": 300, "qsize": 30, "diverge_threshold": 8,
     "regime_mode": "defensive", "use_vol_adjust": True},
    {"product": "VEV_5500", "limit": 300, "qsize": 30, "diverge_threshold": 2,
     "regime_mode": "defensive", "use_vol_adjust": True},
    # peak ATM — combined wins (no gating, full cap captures highest mean)
    {"product": "VEV_5200", "limit": 300, "qsize": 30, "diverge_threshold": 11,
     "regime_mode": "off",       "use_vol_adjust": False},
]


def _vol_factor(scratch, mid):
    """Scale threshold by current_vol / baseline_vol once warmup completes."""
    last_mid = scratch.get("_last_mid", mid)
    diff = mid - last_mid
    scratch["_last_mid"] = mid
    n = scratch.get("vol_n", 0) + 1
    s2 = scratch.get("vol_s2", 0.0) + diff * diff
    scratch["vol_n"], scratch["vol_s2"] = n, s2
    if n <= 100:
        return 1.0
    cur = math.sqrt(s2 / n)
    baseline = scratch.get("_vol_baseline")
    if baseline is None and n > 500:
        scratch["_vol_baseline"] = cur
        baseline = cur
    if baseline is None or baseline <= 0.1:
        return 1.0
    return max(1.0, cur / baseline)


def _anchor_vev_orders(cfg, state, scratch, regime_scale):
    """Divergence-take on |mid - anchor| > threshold + free crosses + MM quote."""
    sym = cfg["product"]
    depth = state.order_depths.get(sym)
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    bb = max(depth.buy_orders)
    ba = min(depth.sell_orders)
    mid = (bb + ba) / 2
    fair = _full_depth_mid(depth)
    pos = state.position.get(sym, 0)
    limit = cfg["limit"]

    n = scratch.get("anchor_n", 0) + 1
    s = scratch.get("anchor_sum", 0.0) + mid
    scratch["anchor_n"], scratch["anchor_sum"] = n, s
    anchor = s / n

    out, bv, sv = [], 0, 0
    accept_any = lambda px: True

    # Divergence-take: trade past per-strike threshold (vol-adjusted optional)
    threshold = cfg["diverge_threshold"]
    if cfg["use_vol_adjust"]:
        threshold *= _vol_factor(scratch, mid)
    diverge = mid - anchor
    if threshold > 0 and n >= ANCHOR_WARMUP and abs(diverge) >= threshold:
        max_pos = max(1, int(MAX_DIVERGE_POS * regime_scale))
        if diverge > 0 and pos > -max_pos:
            qty_target = min(DIVERGE_TAKE_SIZE, limit + pos, pos + max_pos)
            takes, filled = _walk_book(depth, -1, sym, accept_any, qty_target)
            out.extend(takes); sv += filled
        elif diverge < 0 and pos < max_pos:
            qty_target = min(DIVERGE_TAKE_SIZE, limit - pos, max_pos - pos)
            takes, filled = _walk_book(depth, +1, sym, accept_any, qty_target)
            out.extend(takes); bv += filled

    # Free PnL: cross any ask < fair-1 / bid > fair+1 (regardless of divergence)
    pos_eff = pos + bv - sv
    takes, filled = _walk_book(
        depth, +1, sym, lambda px: px < fair - TAKE_WIDTH, limit - pos_eff,
    )
    out.extend(takes); bv += filled
    takes, filled = _walk_book(
        depth, -1, sym, lambda px: px > fair + TAKE_WIDTH, limit + pos_eff,
    )
    out.extend(takes); sv += filled

    # Always-on MM quote between fair and the touch
    qsize = cfg["qsize"]
    bid_px = min(math.floor((fair + bb) / 2), ba - 1)
    ask_px = max(math.ceil((fair + ba) / 2), bb + 1)
    if bid_px < ask_px:
        bq = max(0, min(qsize, limit - pos - bv))
        sq = max(0, min(qsize, limit + pos - sv))
        if bq > 0:
            out.append(Order(sym, bid_px, bq))
        if sq > 0:
            out.append(Order(sym, ask_px, -sq))
    return out


def _defensive_scale(state, store):
    """200-tick rolling spot z-score on VFE: scales position cap down at
    high |z|. Returns 1.0 in normal regime, ~0.30 at |z| >= 1.5."""
    vf_depth = state.order_depths.get("VELVETFRUIT_EXTRACT")
    if not vf_depth or not vf_depth.buy_orders or not vf_depth.sell_orders:
        return 1.0
    spot = (max(vf_depth.buy_orders) + min(vf_depth.sell_orders)) / 2.0

    spot_buf = store.setdefault("_spot_buf", [])
    spot_buf.append(spot)
    if len(spot_buf) > 200:
        del spot_buf[0]
    if len(spot_buf) < 100:
        return 1.0

    mu = sum(spot_buf) / len(spot_buf)
    var = sum((x - mu) ** 2 for x in spot_buf) / len(spot_buf)
    sd = math.sqrt(max(1e-6, var))
    if sd <= 0.5:
        return 1.0
    z = abs(spot - mu) / sd
    if z >= 1.5:
        return 0.30
    if z > 0.5:
        return 1.0 - 0.70 * (z - 0.5)
    return 1.0


# ============================================================================
# Trader
# ============================================================================

class Trader:
    def bid(self):
        return 0

    def run(self, state: TradingState):
        try:
            store = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            store = {}

        orders: dict[str, list[Order]] = {}

        # HP and VFE conviction MR. VFE additionally writes any VEV_5000
        # chain orders directly into `orders`.
        for cfg in (HP_CFG, VFE_CFG):
            ors = _conviction_orders(state, cfg, orders, store)
            if ors:
                orders[cfg["symbol"]] = ors

        # Passive flatten on inherited VEV_5000 inventory (appends to any
        # active chain orders for the same product).
        flatten = _vev_5000_flatten(state)
        if flatten is not None:
            orders.setdefault(flatten.symbol, []).append(flatten)

        # Anchor-divergence vouchers with shared defensive regime scale
        defensive = _defensive_scale(state, store)
        for cfg in ANCHOR_VEV_CFGS:
            scratch = store.setdefault(cfg["product"], {})
            scale = 1.0 if cfg["regime_mode"] == "off" else defensive
            ors = _anchor_vev_orders(cfg, state, scratch, scale)
            if ors:
                orders[cfg["product"]] = ors

        return orders, 0, json.dumps(store)
