"""
Round 4 — hybrid_hp_z (TMP).

HYDROGEL_PACK is run with no_marks's full conviction-MM block (z + EMA
conviction → primary take + aggressive MR take + quoted MM). Every other
product (VFE + all VEV strikes) is run with z_take's pure z-score taker
off the 4-day pooled static mean.

Rationale: per per-asset comparison, no_marks beats z_take by ~$29k on
HP (its quoted MM captures spread on a low-vol product) but z_take beats
no_marks by ~$225k cumulative on VEV_4000-5100 (high-vol strikes where
the static mean is the right model). VFE is a z_take win (~$23k).

The two blocks are isolated:
  - HP block reads only state.order_depths['HYDROGEL_PACK'] and writes
    only td["_hp_*"] keys.
  - z_take block reads only its own product's depth per CFG row.
No cross-product gating, no shared state.
"""

import json
import re
from datamodel import Order, TradingState


# ============================================================================
# Shared book walker
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


# ============================================================================
# HP pipeline — lifted verbatim from no_marks (HP_CFG only)
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
    "hard_cap_end_pct": 0.95, "decay_end_tick": 800000,
    "aggr_mean_mode": "static", "aggr_sd_source": "static",
    "aggr_alpha": 0.002, "aggr_z_thresh": 2.5,
    "aggr_warmup": 300, "aggr_max_take": 90, "aggr_max_take_end": 90,
    "aggr_max_pos_for_fire": 200,
    "aggr_tiers": [],
    "enable_harvest": False, "harvest_window_ticks": 200,
    "harvest_z_thresh": 1.0, "harvest_take_size": 30,
}

MM_BOOST = 1.0


def _conviction(depth, td, cfg):
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


def _aggressive_mr_take(depth, sym, cfg, td, pos, bv_in, sv_in, max_take):
    if cfg["aggr_mean_mode"] == "off" or max_take <= 0:
        return [], 0, 0

    bb = max(depth.buy_orders)
    ba = min(depth.sell_orders)
    mid = (bb + ba) / 2.0
    p = cfg["prefix"]

    if cfg["aggr_mean_mode"] == "static":
        mean = cfg["fair"]
        if cfg.get("aggr_sd_source", "ewma") == "static":
            sd = cfg["stdev_init"]
        else:
            var = td.get(f"_{p}_var", cfg["stdev_init"] ** 2)
            sd = max(cfg["stdev_init"] * 0.15, var ** 0.5)
    else:
        n = td.get(f"_{p}_an", 0) + 1
        td[f"_{p}_an"] = n
        mean = (cfg["aggr_alpha"] * mid
                + (1 - cfg["aggr_alpha"]) * td.get(f"_{p}_arm", mid))
        td[f"_{p}_arm"] = mean
        dev_e = mid - mean
        rvar = ((1 - cfg["var_alpha"]) * td.get(f"_{p}_arv", cfg["stdev_init"] ** 2)
                + cfg["var_alpha"] * dev_e * dev_e)
        td[f"_{p}_arv"] = rvar
        sd = max(cfg["stdev_init"] * 0.15, rvar ** 0.5)
        if n < cfg["aggr_warmup"]:
            return [], 0, 0

    z = (mid - mean) / sd

    tiers = cfg.get("aggr_tiers") or [(cfg["aggr_z_thresh"], max_take)]
    abs_z = abs(z)
    cap = sum(sz for zt, sz in tiers if abs_z >= zt)
    if cap <= 0:
        return [], 0, 0

    if abs(pos) > cfg.get("aggr_max_pos_for_fire", cfg["limit"]):
        return [], 0, 0

    limit = cfg["limit"]
    if z < 0:
        room = max(0, min(cap, limit - pos - bv_in))
        if room <= 0:
            return [], 0, 0
        takes, filled = _walk_book(depth, +1, sym, lambda px: px <= mean, room)
        if filled > 0:
            td[f"_{p}_aggr_dir"] = +1
            td[f"_{p}_harvest_ttl"] = cfg.get("harvest_window_ticks", 0)
        return takes, filled, 0
    room = max(0, min(cap, limit + pos - sv_in))
    if room <= 0:
        return [], 0, 0
    takes, filled = _walk_book(depth, -1, sym, lambda px: px >= mean, room)
    if filled > 0:
        td[f"_{p}_aggr_dir"] = -1
        td[f"_{p}_harvest_ttl"] = cfg.get("harvest_window_ticks", 0)
    return takes, 0, filled


def _conviction_orders(state, cfg, td):
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
    within_day = state.timestamp % 1_000_000
    decay_t = min(1.0, max(0.0, within_day / cfg["decay_end_tick"]))
    hc_pct_now = (cfg["hard_cap_pct"]
                  + (cfg["hard_cap_end_pct"] - cfg["hard_cap_pct"]) * decay_t)
    hard_cap = hc_pct_now * limit
    aggr_max_now = int(round(cfg["aggr_max_take"]
                             + (cfg["aggr_max_take_end"] - cfg["aggr_max_take"]) * decay_t))

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
            out.extend(takes); bv += filled
        elif direction < 0 and pos_after > -soft_cap:
            qty_target = min(primary_target, limit + pos - sv, int(soft_cap + pos_after))
            takes, filled = _walk_book(
                depth, -1, sym, lambda px: px >= fair - offset, qty_target,
            )
            out.extend(takes); sv += filled

    if abs(pos) < hard_cap:
        aggr_orders, abv, asv = _aggressive_mr_take(
            depth, sym, cfg, td, pos, bv, sv, aggr_max_now,
        )
        if aggr_orders:
            out.extend(aggr_orders); bv += abv; sv += asv

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


# ============================================================================
# z_take pipeline — VFE + every VEV strike (4-day pooled mean/sd, z=1.0)
# ============================================================================

ZTAKE_CFGS = [
    {"symbol": "VELVETFRUIT_EXTRACT", "mean": 5247, "sd": 17.091, "z_thresh": 1.0, "take_size": 50, "limit": 200},
    {"symbol": "VEV_4000",            "mean": 1247, "sd": 17.114, "z_thresh": 1.0, "take_size": 50, "limit": 300},
    {"symbol": "VEV_4500",            "mean":  747, "sd": 17.105, "z_thresh": 1.0, "take_size": 50, "limit": 300},
    {"symbol": "VEV_5000",            "mean":  252, "sd": 16.381, "z_thresh": 1.0, "take_size": 50, "limit": 300},
    {"symbol": "VEV_5100",            "mean":  163, "sd": 15.327, "z_thresh": 1.0, "take_size": 50, "limit": 300},
    {"symbol": "VEV_5200",            "mean":   91, "sd": 12.796, "z_thresh": 1.0, "take_size": 50, "limit": 300},
    {"symbol": "VEV_5300",            "mean":   43, "sd":  8.976, "z_thresh": 1.0, "take_size": 50, "limit": 300},
    {"symbol": "VEV_5400",            "mean":   14, "sd":  4.608, "z_thresh": 1.0, "take_size": 50, "limit": 300},
    {"symbol": "VEV_5500",            "mean":    6, "sd":  2.477, "z_thresh": 1.0, "take_size": 50, "limit": 300},
]


def _z_take_orders(state, cfg):
    sym = cfg["symbol"]
    depth = state.order_depths.get(sym)
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    mid = (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0
    mean, sd = cfg["mean"], cfg["sd"]
    if sd <= 0:
        return []
    z = (mid - mean) / sd
    if abs(z) < cfg["z_thresh"]:
        return []

    pos = state.position.get(sym, 0)
    limit = cfg["limit"]
    take_size = cfg["take_size"]

    if z > 0:
        room = max(0, min(take_size, limit + pos))
        if room <= 0:
            return []
        orders, _ = _walk_book(depth, -1, sym, lambda px: px >= mean, room)
        return orders
    room = max(0, min(take_size, limit - pos))
    if room <= 0:
        return []
    orders, _ = _walk_book(depth, +1, sym, lambda px: px <= mean, room)
    return orders


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

        hp_orders = _conviction_orders(state, HP_CFG, store)
        if hp_orders:
            orders[HP_CFG["symbol"]] = hp_orders

        for cfg in ZTAKE_CFGS:
            ors = _z_take_orders(state, cfg)
            if ors:
                orders[cfg["symbol"]] = ors

        return orders, 0, json.dumps(store)
