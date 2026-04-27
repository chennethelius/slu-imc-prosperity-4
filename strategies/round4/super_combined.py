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
    - VEV_4000, 4500, 5400  from v01_named_informed_mm (gating off, base thr)
    - VEV_5100, 5300, 5500  from combined (defensive regime + vol adjust)
    - VEV_5200              from combined (peak ATM — no gating, full cap)

Backtest (QP=1.0):
                       D1       D2       D3      mean      min   mean+min
  super_combined    241,157  151,064  157,378  183,200  151,064    334,264

==============================================================================
PIPELINE 1 — CONVICTION MR
==============================================================================
Composite signal blending three components (computed per primary product):

  z_str    = clipped (|mid - fair| / stdev) normalized to [0, 1]
  ema_str  = sign-aligned (ema_fast - ema_slow) magnitude when fast/slow/mid
             agree on direction; otherwise zero
  inf_str  = signed sum of named-counterparty trades over a recent window,
             only when its sign matches the dev direction
  conv     = w_z * z_str + w_ema * ema_str + w_inf * inf_str   when z_str > 0

Drives:
  - Primary take volume = take_max * conv (cross up to fair +/- take_offset)
  - Position cap dyn_cap_pct linear from base_cap to full_cap (with conv)
  - MM-leg quote size scaled by (1 + MM_BOOST * conv) on alpha-aligned side
  - For VFE: any unfilled primary take volume routes to VEV_5000 via the
    delta-extension chain (intrinsic +/- offset, dynamic soft_cap with conv)

==============================================================================
PIPELINE 2 — ANCHOR DIVERGENCE
==============================================================================
Per voucher: maintain a rolling-mean anchor of the mid; trade when the
current mid diverges past a per-strike threshold. Flags per voucher:

  regime_mode   : "off" | "defensive" | "recovery"
  use_vol_adjust: scale threshold up by current_vol / baseline_vol
  use_asymm     : raise threshold for ADD trades, keep base for REDUCE

The "regime_mode" applies a global scale to position cap based on a
200-tick rolling z-score of VFE spot, with a 500-tick rolling-min tracker
for V-bottom recovery detection (round-4 D3 q4 +37-tick rebound):
  defensive  : scale down to 0.30 at |z| >= 1.5 (always defensive)
  recovery   : scale down on falls, scale up to 1.3 once spot reverses
  off        : always 1.0 (no gating)

==============================================================================
"""

import json
import math
from datamodel import Order, TradingState


# ============================================================================
# Common helpers
# ============================================================================

def _search_sells(depth):
    for p in sorted(depth.sell_orders):
        yield p, -depth.sell_orders[p]


def _search_buys(depth):
    for p in sorted(depth.buy_orders, reverse=True):
        yield p, depth.buy_orders[p]


def _full_depth_mid(depth):
    bids, asks = list(_search_buys(depth)), list(_search_sells(depth))
    bv, av = sum(v for _, v in bids), sum(v for _, v in asks)
    if bv <= 0 or av <= 0:
        return (max(depth.buy_orders) + min(depth.sell_orders)) / 2
    return (sum(p * v for p, v in bids) / bv + sum(p * v for p, v in asks) / av) / 2


# ============================================================================
# Conviction MR — HYDROGEL_PACK, VELVETFRUIT_EXTRACT, and VFE -> VEV_5000 chain
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
# -> 5000 -> 5100 -> 5200; we own only VEV_5000 here, so the chain has a
# single target. The other ITM strikes are filled by the anchor pipeline.
VFE_DELTA_VEV_5000 = {
    "symbol": "VEV_5000", "strike": 5000, "limit": 300,
    "soft_cap": 200, "soft_cap_max": 250, "offset": 8,
}

MM_BOOST = 1.0  # alpha-aligned MM quote size multiplier at conv=1.0


def _conviction_signals(depth, td, market_trades, cfg):
    """z + EMA-trend + counterparty-flow -> composite conviction in [0, 1]."""
    if not depth.buy_orders or not depth.sell_orders:
        return None
    bb = max(depth.buy_orders)
    ba = min(depth.sell_orders)
    mid = (bb + ba) / 2.0
    p, fair = cfg["prefix"], cfg["fair"]

    # z-score deviation, magnitude in [0, 1]
    dev = mid - fair
    var = td.get(f"_{p}_var", cfg["stdev_init"] ** 2)
    var = (1.0 - cfg["var_alpha"]) * var + cfg["var_alpha"] * (dev * dev)
    td[f"_{p}_var"] = var
    stdev = max(cfg["stdev_init"] * 0.15, var ** 0.5)
    z = abs(dev / stdev)
    z_str = 0.0 if z < cfg["z_min"] else min(1.0, (z - cfg["z_min"]) / (cfg["z_max"] - cfg["z_min"]))
    direction = +1 if dev < 0 else -1

    # EMA-trend confirmation: counts only when fast/slow/very-slow agree with dev
    ema_f = cfg["ema_fast"] * mid + (1 - cfg["ema_fast"]) * td.get(f"_{p}_ef", mid)
    ema_s = cfg["ema_slow"] * mid + (1 - cfg["ema_slow"]) * td.get(f"_{p}_es", mid)
    ema_vs = cfg["ema_vslow"] * mid + (1 - cfg["ema_vslow"]) * td.get(f"_{p}_evs", mid)
    td[f"_{p}_ef"], td[f"_{p}_es"], td[f"_{p}_evs"] = ema_f, ema_s, ema_vs
    short = ema_f - ema_s
    medium = ema_s - ema_vs
    short_sign = (short > 0) - (short < 0)
    medium_sign = (medium > 0) - (medium < 0)
    if short_sign != 0 and short_sign == medium_sign and short_sign == direction:
        ema_str = min(1.0, abs(short) / cfg["ema_full"])
    else:
        ema_str = 0.0

    # Named-counterparty net flow over recent window
    net_inf = 0.0
    if market_trades:
        weights = cfg["mark_weights"]
        for t in market_trades[-cfg["informed_lookback"]:]:
            qty = int(t.quantity)
            net_inf += weights.get(t.buyer or "", 0.0) * qty
            net_inf -= weights.get(t.seller or "", 0.0) * qty
    inf_sign = (net_inf > 0) - (net_inf < 0)
    inf_str = (min(1.0, abs(net_inf) / cfg["informed_full"])
               if inf_sign != 0 and inf_sign == direction else 0.0)

    conv = (cfg["w_z"] * z_str + cfg["w_ema"] * ema_str + cfg["w_inf"] * inf_str
            if z_str > 0 else 0.0)
    return {"bb": bb, "ba": ba, "mid": mid, "direction": direction, "conviction": conv}


def _conviction_orders(state, cfg, all_orders, td):
    """Conviction MR for a primary product (HP or VFE).

    1. Hard-cap unwind if position outside hard_cap_pct * limit.
    2. Conviction-scaled primary take up to fair +/- take_offset.
    3. (VFE only) Route any unfilled primary-take overflow into VEV_5000.
    4. Always-on MM leg with alpha-aligned conviction-boost on size.
    """
    sym = cfg["symbol"]
    depth = state.order_depths.get(sym)
    if depth is None:
        return []
    pos = state.position.get(sym, 0)
    market_trades = state.market_trades.get(sym, [])

    sig = _conviction_signals(depth, td, market_trades, cfg)
    if sig is None:
        return []
    bb, ba, mid = sig["bb"], sig["ba"], sig["mid"]
    direction, conv = sig["direction"], sig["conviction"]
    limit, fair = cfg["limit"], cfg["fair"]
    hard_cap = cfg["hard_cap_pct"] * limit

    out, bv, sv = [], 0, 0

    # 1. Hard-cap unwind toward fair when |pos| breaches hard_cap_pct * limit
    if pos > hard_cap:
        for bid in sorted(depth.buy_orders, reverse=True):
            if bid < fair - 2:
                break
            qty = min(depth.buy_orders[bid], pos, limit + pos - sv)
            if qty <= 0:
                break
            out.append(Order(sym, bid, -qty))
            sv += qty
            if pos + bv - sv <= hard_cap * 0.5:
                break
    elif pos < -hard_cap:
        for ask in sorted(depth.sell_orders):
            if ask > fair + 2:
                break
            qty = min(-depth.sell_orders[ask], -pos, limit - pos - bv)
            if qty <= 0:
                break
            out.append(Order(sym, ask, qty))
            bv += qty
            if pos + bv - sv >= -hard_cap * 0.5:
                break

    pos_after = pos + bv - sv
    soft_cap = (cfg["base_cap_pct"] + (cfg["full_cap_pct"] - cfg["base_cap_pct"]) * conv) * limit

    # 2. Conviction-scaled primary take
    primary_target = int(round(cfg["take_max"] * conv)) if conv > 0 else 0
    primary_taken = 0
    if conv > 0 and direction > 0 and pos_after < soft_cap:
        max_pay = fair + cfg["take_offset"]
        rem = primary_target
        for ask in sorted(depth.sell_orders):
            if ask > max_pay or rem <= 0:
                break
            qty = min(-depth.sell_orders[ask], limit - pos - bv,
                      int(soft_cap - pos_after), rem)
            if qty <= 0:
                break
            out.append(Order(sym, ask, qty))
            bv += qty
            rem -= qty
            primary_taken += qty
            pos_after = pos + bv - sv
    elif conv > 0 and direction < 0 and pos_after > -soft_cap:
        min_recv = fair - cfg["take_offset"]
        rem = primary_target
        for bid in sorted(depth.buy_orders, reverse=True):
            if bid < min_recv or rem <= 0:
                break
            qty = min(depth.buy_orders[bid], limit + pos - sv,
                      int(soft_cap + pos_after), rem)
            if qty <= 0:
                break
            out.append(Order(sym, bid, -qty))
            sv += qty
            rem -= qty
            primary_taken += qty
            pos_after = pos + bv - sv

    # 3. VFE -> VEV_5000 chain extension (overflow routing)
    if cfg["prefix"] == "vfe" and conv > 0 and primary_target > primary_taken:
        overflow = primary_target - primary_taken
        v = VFE_DELTA_VEV_5000
        v_depth = state.order_depths.get(v["symbol"])
        if v_depth and v_depth.buy_orders and v_depth.sell_orders:
            v_pos = state.position.get(v["symbol"], 0)
            intrinsic = int(round(mid)) - v["strike"]
            v_soft = int(round(v["soft_cap"] + (v["soft_cap_max"] - v["soft_cap"]) * conv))
            v_orders, v_bv, v_sv = [], 0, 0
            if direction > 0 and v_pos < v_soft:
                max_pay = intrinsic + v["offset"]
                for ask in sorted(v_depth.sell_orders):
                    if ask > max_pay or overflow <= 0:
                        break
                    qty = min(-v_depth.sell_orders[ask], v["limit"] - v_pos - v_bv,
                              max(0, v_soft - v_pos), overflow)
                    if qty <= 0:
                        break
                    v_orders.append(Order(v["symbol"], ask, qty))
                    v_bv += qty
                    overflow -= qty
            elif direction < 0 and v_pos > -v_soft:
                min_recv = intrinsic - v["offset"]
                for bid in sorted(v_depth.buy_orders, reverse=True):
                    if bid < min_recv or overflow <= 0:
                        break
                    qty = min(v_depth.buy_orders[bid], v["limit"] + v_pos - v_sv,
                              max(0, v_soft + v_pos), overflow)
                    if qty <= 0:
                        break
                    v_orders.append(Order(v["symbol"], bid, -qty))
                    v_sv += qty
                    overflow -= qty
            if v_orders:
                all_orders[v["symbol"]] = v_orders

    # 4. MM leg with alpha-aligned conviction boost
    pos_after = pos + bv - sv
    mr_dir = +1 if mid < fair - cfg["mr_thresh"] else (-1 if mid > fair + cfg["mr_thresh"] else 0)
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
            bm *= (1.0 + MM_BOOST * conv)
        elif direction < 0:
            sm *= (1.0 + MM_BOOST * conv)
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
    vfe_mid_int = int(round((max(vfe_depth.buy_orders) + min(vfe_depth.sell_orders)) / 2.0))
    intrinsic = vfe_mid_int - v["strike"]
    v_bb, v_ba = max(v_depth.buy_orders), min(v_depth.sell_orders)
    if v_pos > 0:
        ask_px = max(intrinsic + 1, v_ba - 1)
        sell_q = min(v_pos, 50, v["limit"] + v_pos)
        if sell_q > 0:
            return Order(v["symbol"], ask_px, -sell_q)
    bid_px = min(intrinsic - 1, v_bb + 1)
    buy_q = min(-v_pos, 50, v["limit"] - v_pos)
    if buy_q > 0:
        return Order(v["symbol"], bid_px, buy_q)
    return None


# ============================================================================
# Anchor divergence — VEV_4000, 4500, 5100, 5200, 5300, 5400, 5500
# ============================================================================

TAKE_WIDTH = 1
ANCHOR_WARMUP = 100
DIVERGE_TAKE_SIZE = 30

# Per-asset config. The mode column reflects the audit winner:
#   v01-style:  off + base threshold (no gating, higher z-thresh)
#   cmb-style:  defensive + vol-adjusted (OTM strikes; gate handles spot)
#   peak (5200): off + no vol adjust (full cap captures highest mean PnL)
ANCHOR_VEV_CFGS = [
    # v01-style — no gating, base threshold sized for ITM premium decay
    {"product": "VEV_4000", "limit": 300, "qsize": 30, "diverge_threshold": 25,
     "max_diverge_position": 295,
     "regime_mode": "off",       "use_vol_adjust": False, "use_asymm": False},
    {"product": "VEV_4500", "limit": 300, "qsize": 30, "diverge_threshold": 25,
     "max_diverge_position": 295,
     "regime_mode": "off",       "use_vol_adjust": False, "use_asymm": False},
    {"product": "VEV_5400", "limit": 300, "qsize": 30, "diverge_threshold": 5,
     "max_diverge_position": 295,
     "regime_mode": "off",       "use_vol_adjust": False, "use_asymm": False},
    # cmb-style — defensive regime + vol-adjusted threshold (OTM)
    {"product": "VEV_5100", "limit": 300, "qsize": 30, "diverge_threshold": 14,
     "max_diverge_position": 295,
     "regime_mode": "defensive", "use_vol_adjust": True,  "use_asymm": False},
    {"product": "VEV_5300", "limit": 300, "qsize": 30, "diverge_threshold": 8,
     "max_diverge_position": 295,
     "regime_mode": "defensive", "use_vol_adjust": True,  "use_asymm": False},
    {"product": "VEV_5500", "limit": 300, "qsize": 30, "diverge_threshold": 2,
     "max_diverge_position": 295,
     "regime_mode": "defensive", "use_vol_adjust": True,  "use_asymm": False},
    # peak-mean — full cap, no gating
    {"product": "VEV_5200", "limit": 300, "qsize": 30, "diverge_threshold": 11,
     "max_diverge_position": 295,
     "regime_mode": "off",       "use_vol_adjust": False, "use_asymm": False},
]


def _divergence_take(cfg, depth, scratch, position, anchor, mid, regime_scale):
    """Take when |mid - anchor| > threshold; threshold can be vol-adjusted
    and asymmetric (raised for trades that ADD to existing position)."""
    base_threshold = cfg["diverge_threshold"]

    # Track tick-to-tick mid std and a baseline locked at the warmup point.
    last_mid = scratch.get("_last_mid", mid)
    diff = mid - last_mid
    scratch["_last_mid"] = mid
    vol_n = scratch.get("vol_n", 0) + 1
    vol_s2 = scratch.get("vol_s2", 0.0) + diff * diff
    scratch["vol_n"], scratch["vol_s2"] = vol_n, vol_s2
    vol_factor = 1.0
    if cfg["use_vol_adjust"] and vol_n > 100:
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
    # REDUCE-side keeps base threshold (don't slow down unwinds).
    if cfg["use_asymm"]:
        add_factor = 1.0 + 2.0 * abs(position) / max(1, cfg["limit"])
        is_reducing = (diverge > 0 and position > 0) or (diverge < 0 and position < 0)
        threshold = base_threshold * vol_factor * (1.0 if is_reducing else add_factor)
    else:
        threshold = base_threshold * vol_factor
    if abs(diverge) < threshold:
        return [], 0, 0

    product, limit = cfg["product"], cfg["limit"]
    max_pos = max(1, int(cfg["max_diverge_position"] * regime_scale))
    out, bought, sold = [], 0, 0
    if diverge > 0 and position > -max_pos:
        room = position + max_pos
        for price, qty in _search_buys(depth):
            cap = min(limit + position - sold, DIVERGE_TAKE_SIZE - sold, room - sold)
            if cap <= 0:
                break
            take = min(qty, cap)
            out.append(Order(product, price, -take))
            sold += take
    elif diverge < 0 and position < max_pos:
        room = max_pos - position
        for price, qty in _search_sells(depth):
            cap = min(limit - position - bought, DIVERGE_TAKE_SIZE - bought, room - bought)
            if cap <= 0:
                break
            take = min(qty, cap)
            out.append(Order(product, price, take))
            bought += take
    return out, bought, sold


def _take_at_fair(cfg, depth, fair, position):
    """Cross any ask < fair - 1 / bid > fair + 1 — free PnL."""
    product, limit = cfg["product"], cfg["limit"]
    out, bought, sold = [], 0, 0
    for price, qty in _search_sells(depth):
        if price >= fair - TAKE_WIDTH:
            break
        cap = limit - position - bought
        if cap <= 0:
            break
        take = min(qty, cap)
        out.append(Order(product, price, take))
        bought += take
    for price, qty in _search_buys(depth):
        if price <= fair + TAKE_WIDTH:
            break
        cap = limit + position - sold
        if cap <= 0:
            break
        take = min(qty, cap)
        out.append(Order(product, price, -take))
        sold += take
    return out, bought, sold


def _quote_inside(cfg, fair, best_bid, best_ask, position, bought, sold):
    """Always-on MM leg quoted halfway between fair and the touch."""
    product, limit, qsize = cfg["product"], cfg["limit"], cfg["qsize"]
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


def _anchor_vev_orders(cfg, state, scratch, regime_scale):
    """Divergence-take + free-PnL crosses + always-on MM quote."""
    depth = state.order_depths.get(cfg["product"])
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    best_bid, best_ask = max(depth.buy_orders), min(depth.sell_orders)
    mid = (best_bid + best_ask) / 2
    fair = _full_depth_mid(depth)

    n = scratch.get("anchor_n", 0) + 1
    s = scratch.get("anchor_sum", 0.0) + mid
    scratch["anchor_n"], scratch["anchor_sum"] = n, s
    anchor = s / n
    position = state.position.get(cfg["product"], 0)

    diverge_orders, d_bought, d_sold = _divergence_take(
        cfg, depth, scratch, position, anchor, mid, regime_scale
    )
    pos_eff = position + d_bought - d_sold
    take_orders, t_bought, t_sold = _take_at_fair(cfg, depth, fair, pos_eff)
    quotes = _quote_inside(cfg, fair, best_bid, best_ask, position,
                           d_bought + t_bought, d_sold + t_sold)
    return diverge_orders + take_orders + quotes


def _compute_regime_scales(state, store):
    """200-tick rolling spot z-score on VFE -> defensive + recovery scales.

    A 500-tick rolling-min tracker detects V-bottom recoveries (round-4 D3
    pattern: spot drops 63 ticks q1-q2, troughs at $5191, then rebounds
    +37 ticks back to mean by q4). When in recovery (>=50 ticks past the
    rolling-min and spot >=3 ticks above it), recovery_scale boosts the
    position cap to 1.3 instead of throttling.
    """
    vf_depth = state.order_depths.get("VELVETFRUIT_EXTRACT")
    if not vf_depth or not vf_depth.buy_orders or not vf_depth.sell_orders:
        return 1.0, 1.0
    spot = (max(vf_depth.buy_orders) + min(vf_depth.sell_orders)) / 2.0

    spot_buf = store.setdefault("_spot_buf", [])
    spot_buf.append(spot)
    if len(spot_buf) > 200:
        del spot_buf[0]

    min_tracker = store.setdefault("_min_tracker", {"min": spot, "ts": state.timestamp})
    if spot < min_tracker["min"]:
        min_tracker["min"] = spot
        min_tracker["ts"] = state.timestamp
    ticks_since_min = (state.timestamp - min_tracker["ts"]) // 100
    if ticks_since_min > 500 and len(spot_buf) >= 50:
        min_tracker["min"] = min(spot_buf[-50:])
        min_tracker["ts"] = state.timestamp
        ticks_since_min = 0

    defensive_scale = recovery_scale = 1.0
    if len(spot_buf) >= 100:
        mu = sum(spot_buf) / len(spot_buf)
        var = sum((x - mu) ** 2 for x in spot_buf) / len(spot_buf)
        sd = math.sqrt(max(1e-6, var))
        if sd > 0.5:
            z = abs(spot - mu) / sd
            in_recovery = ticks_since_min >= 50 and spot >= min_tracker["min"] + 3.0
            if z >= 1.5:
                defensive_scale = 0.30
                recovery_scale = 1.3 if in_recovery else 0.30
            elif z > 0.5:
                defensive_scale = 1.0 - 0.70 * (z - 0.5)
                recovery_scale = (1.0 + 0.20 * (z - 0.5) if in_recovery
                                  else 1.0 - 0.70 * (z - 0.5))
    return defensive_scale, recovery_scale


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

        # Conviction MR for HP and VFE; VFE additionally writes any owned
        # VEV_5000 chain orders directly into the orders dict.
        for cfg in (HP_CFG, VFE_CFG):
            sym = cfg["symbol"]
            ors = _conviction_orders(state, cfg, orders, store)
            if ors:
                orders[sym] = ors

        # Passive flatten on inherited VEV_5000 inventory (won't override
        # active chain entries from the VFE pipeline).
        flatten = _vev_5000_flatten(state)
        if flatten is not None:
            orders.setdefault(flatten.symbol, []).append(flatten)

        # Anchor-divergence vouchers with shared regime gating
        defensive_scale, recovery_scale = _compute_regime_scales(state, store)
        regime_lookup = {
            "off": 1.0,
            "defensive": defensive_scale,
            "recovery": recovery_scale,
        }
        for cfg in ANCHOR_VEV_CFGS:
            scratch = store.setdefault(cfg["product"], {})
            scale = regime_lookup[cfg["regime_mode"]]
            ors = _anchor_vev_orders(cfg, state, scratch, scale)
            if ors:
                orders[cfg["product"]] = ors

        return orders, 0, json.dumps(store)
