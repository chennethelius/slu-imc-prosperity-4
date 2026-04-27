"""
Round 4 — no_marks: super_combined with all counterparty (Mark) signals
stripped, plus VEV_6000/6500 OTM extension lifted from v77.

Pure market analysis only — z-score, EMA-trend, anchor-divergence,
vol-adjusted thresholds, defensive spot-z regime gate. No reading of
state.market_trades buyer/seller fields anywhere.

Per-asset strategy ownership (unchanged from super_combined except OTM):

  Conviction MR (z + EMA only)
    - HYDROGEL_PACK         from v41_mm_conviction_boost (no inf_str)
    - VELVETFRUIT_EXTRACT   from v41_mm_conviction_boost (no inf_str)
    - VEV_5000              from v52_full_cap_high_conv  (chain extension)

  Anchor divergence
    - VEV_4000, 4500, 5400  no gating, base threshold
    - VEV_5100, 5300, 5500  defensive z-gate + vol-adjusted threshold
    - VEV_5200              no gating, full cap (peak ATM)
    - VEV_6000, 6500        no gating (deep OTM, additive from v77)

Conviction weights rebalanced after dropping w_inf=0.20:
  was: w_z=0.50, w_ema=0.30, w_inf=0.20  (peak conv = 1.0)
  now: w_z=0.625, w_ema=0.375            (peak conv = 1.0, same 5:3 ratio)

This preserves the soft_cap_pct / MM_BOOST tuning that assumes
conv ∈ [0, 1]. The relative z-vs-ema weighting is unchanged.

PRE-SUBMISSION:
  Backtest at QP=1.0, 3 days, score by per-day mean+min vs super_combined.
  If mean+min stays within ~10% of super_combined (341,093) the no-Mark
  hypothesis holds — promote. If the gap is large, the inf_str term was
  carrying real edge and the rebalance is wrong.
"""

import json
import math
from datamodel import Order, TradingState


# ============================================================================
# Book walker
# ============================================================================

def _walk_book(depth, side, sym, ok, qty_target):
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
# Pipeline 1 — Conviction MR (z + EMA only)
# ============================================================================

HP_CFG = {
    "prefix": "hp", "symbol": "HYDROGEL_PACK", "limit": 200, "fair": 10002,
    "stdev_init": 33.0, "var_alpha": 0.005,
    "qsize": 35, "flat_pull": 1.0, "mr_thresh": 4, "mr_boost": 1.5,
    "z_min": 0.7, "z_max": 2.0,
    "ema_fast": 0.30, "ema_slow": 0.05, "ema_vslow": 0.02, "ema_full": 1.5,
    "w_z": 0.625, "w_ema": 0.375,
    "take_max": 80, "take_offset": 4,
    "base_cap_pct": 0.50, "full_cap_pct": 1.00, "hard_cap_pct": 0.95,
}

VFE_CFG = {
    "prefix": "vfe", "symbol": "VELVETFRUIT_EXTRACT", "limit": 200, "fair": 5249,
    "stdev_init": 17.0, "var_alpha": 0.005,
    "qsize": 30, "flat_pull": 1.0, "mr_thresh": 3, "mr_boost": 1.5,
    "z_min": 0.7, "z_max": 2.0,
    "ema_fast": 0.30, "ema_slow": 0.05, "ema_vslow": 0.02, "ema_full": 0.8,
    "w_z": 0.625, "w_ema": 0.375,
    "take_max": 70, "take_offset": 3,
    "base_cap_pct": 0.50, "full_cap_pct": 1.00, "hard_cap_pct": 0.95,
}

VFE_DELTA_VEV_5000 = {
    "symbol": "VEV_5000", "strike": 5000, "limit": 300,
    "soft_cap": 200, "soft_cap_max": 250, "offset": 8,
}

MM_BOOST = 1.0


def _conviction(depth, td, cfg):
    """z + EMA-trend -> composite conviction in [0, 1]. No counterparty input."""
    if not depth.buy_orders or not depth.sell_orders:
        return None
    bb = max(depth.buy_orders)
    ba = min(depth.sell_orders)
    mid = (bb + ba) / 2.0
    p, fair = cfg["prefix"], cfg["fair"]

    dev = mid - fair
    var = ((1.0 - cfg["var_alpha"]) * td.get(f"_{p}_var", cfg["stdev_init"] ** 2)
           + cfg["var_alpha"] * dev * dev)
    td[f"_{p}_var"] = var
    stdev = max(cfg["stdev_init"] * 0.15, var ** 0.5)
    z = abs(dev / stdev)
    z_str = (0.0 if z < cfg["z_min"]
             else min(1.0, (z - cfg["z_min"]) / (cfg["z_max"] - cfg["z_min"])))
    direction = +1 if dev < 0 else -1

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

    conv = (cfg["w_z"] * z_str + cfg["w_ema"] * ema_str
            if z_str > 0 else 0.0)
    return bb, ba, mid, direction, conv


def _conviction_orders(state, cfg, all_orders, td):
    sym = cfg["symbol"]
    depth = state.order_depths.get(sym)
    if depth is None:
        return []
    sig = _conviction(depth, td, cfg)
    if sig is None:
        return []
    bb, ba, mid, direction, conv = sig
    pos = state.position.get(sym, 0)
    limit, fair = cfg["limit"], cfg["fair"]
    hard_cap = cfg["hard_cap_pct"] * limit

    out, bv, sv = [], 0, 0

    if pos > hard_cap:
        target = pos - int(hard_cap * 0.5)
        unw, filled = _walk_book(depth, -1, sym, lambda px: px >= fair - 2, target)
        out.extend(unw); sv += filled
    elif pos < -hard_cap:
        target = -pos - int(hard_cap * 0.5)
        unw, filled = _walk_book(depth, +1, sym, lambda px: px <= fair + 2, target)
        out.extend(unw); bv += filled

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
# Pipeline 2 — Anchor divergence (no counterparty gating anywhere)
# ============================================================================

TAKE_WIDTH = 1
ANCHOR_WARMUP = 500
DIVERGE_TAKE_SIZE = 30
MAX_DIVERGE_POS = 295

ANCHOR_VEV_CFGS = [
    {"product": "VEV_4000", "limit": 300, "qsize": 30, "diverge_threshold": 25,
     "regime_mode": "off",       "use_vol_adjust": False},
    {"product": "VEV_4500", "limit": 300, "qsize": 30, "diverge_threshold": 25,
     "regime_mode": "off",       "use_vol_adjust": False},
    {"product": "VEV_5400", "limit": 300, "qsize": 30, "diverge_threshold": 5,
     "regime_mode": "off",       "use_vol_adjust": False},
    {"product": "VEV_5100", "limit": 300, "qsize": 30, "diverge_threshold": 14,
     "regime_mode": "defensive", "use_vol_adjust": True},
    {"product": "VEV_5300", "limit": 300, "qsize": 30, "diverge_threshold": 8,
     "regime_mode": "defensive", "use_vol_adjust": True},
    {"product": "VEV_5500", "limit": 300, "qsize": 30, "diverge_threshold": 2,
     "regime_mode": "defensive", "use_vol_adjust": True},
    {"product": "VEV_5200", "limit": 300, "qsize": 30, "diverge_threshold": 11,
     "regime_mode": "off",       "use_vol_adjust": False},
    {"product": "VEV_6000", "limit": 300, "qsize": 30, "diverge_threshold": 2,
     "regime_mode": "off",       "use_vol_adjust": False},
    {"product": "VEV_6500", "limit": 300, "qsize": 30, "diverge_threshold": 2,
     "regime_mode": "off",       "use_vol_adjust": False},
]


def _vol_factor(scratch, mid):
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

    pos_eff = pos + bv - sv
    takes, filled = _walk_book(
        depth, +1, sym, lambda px: px < fair - TAKE_WIDTH, limit - pos_eff,
    )
    out.extend(takes); bv += filled
    takes, filled = _walk_book(
        depth, -1, sym, lambda px: px > fair + TAKE_WIDTH, limit + pos_eff,
    )
    out.extend(takes); sv += filled

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

        for cfg in (HP_CFG, VFE_CFG):
            ors = _conviction_orders(state, cfg, orders, store)
            if ors:
                orders[cfg["symbol"]] = ors

        flatten = _vev_5000_flatten(state)
        if flatten is not None:
            orders.setdefault(flatten.symbol, []).append(flatten)

        defensive = _defensive_scale(state, store)
        for cfg in ANCHOR_VEV_CFGS:
            scratch = store.setdefault(cfg["product"], {})
            scale = 1.0 if cfg["regime_mode"] == "off" else defensive
            ors = _anchor_vev_orders(cfg, state, scratch, scale)
            if ors:
                orders[cfg["product"]] = ors

        return orders, 0, json.dumps(store)
