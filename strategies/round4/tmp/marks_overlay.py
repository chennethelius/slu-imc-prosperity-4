"""
Round 4 — marks_overlay (TMP) — FAILED EXPERIMENT, kept for record.

Hypothesis: per-counterparty forward alpha (scripts/mark_alpha.py) shows
strong t-stats, so cross-and-take when fresh Mark signals appear:
  HYDROGEL_PACK  — follow Mark 14, fade Mark 38   (±$8 alpha)
  VFE            — follow Mark 14, fade Mark 55   (~$2.3 alpha)
  VEV_5300/4/5   — fade Mark 22 sells              (~$0.5 alpha)

Result: catastrophic on round4 d1-d3 vs no_marks.py baseline:
  d1: 176,562  vs 242,973  (-27%)
  d2: -392,137 vs 152,734  (HP  -$508k swing)
  d3: -115,207 vs 178,025  (HP  -$268k swing)

Why it broke (counterparty pairing audit, day 2 raw trades):
  HYDROGEL_PACK: 303 of 311 prints are Mark 14 <-> Mark 38 (97%)
  VEV_4000:      128 of 128 prints are Mark 14 <-> Mark 38 (100%)
  VEV_5500:       91 of 91 Mark 22 sells go to Mark 01 (100%)
  VFE:            210 of 477 prints are Mark 55 <-> Mark 14/01 cluster

The "alpha" is spurious — a fully-paired two-way MM, by construction,
trades against price drift between their alternating prints. When we
cross-and-take to "follow Mark 14 buys", we lift the offer at the local
peak; price reverts, and we lose the difference.

Actionable takeaway: Mark 14/38 on HP/VEV_4000 carry zero exploitable
flow signal (already noted in v85 comment "two-way MMs on these (no edge)").
Mark 22 sell-fade is also unreachable as takes because Mark 01 absorbs
100% of the liquidity at the resting price before the print arrives.

Possible follow-ups (not implemented here):
  * Use Mark 14's trade prices as a FAIR-VALUE ANCHOR (mean of recent
    Mark trades = current "true" mid), not a flow signal.
  * Quote tighter than Mark 01 on VEV_5400/5500 to intercept Mark 22
    sells before Mark 01 does (passive, not take).
"""

import json
import math
from datamodel import Order, TradingState


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
    "aggr_mean_mode": "static",
    "aggr_sd_source": "static",
    "aggr_alpha": 0.002, "aggr_z_thresh": 2.5,
    "aggr_warmup": 300, "aggr_max_take": 90, "aggr_max_take_end": 90,
    "aggr_max_pos_for_fire": 200,
    "aggr_tiers": [],
    "enable_harvest": False, "harvest_window_ticks": 200,
    "harvest_z_thresh": 1.0, "harvest_take_size": 30,
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
    "hard_cap_end_pct": 0.95, "decay_end_tick": 800000,
    "aggr_mean_mode": "static",
    "aggr_sd_source": "static",
    "aggr_alpha": 0.002, "aggr_z_thresh": 2.5,
    "aggr_warmup": 300, "aggr_max_take": 90, "aggr_max_take_end": 90,
    "aggr_max_pos_for_fire": 200,
    "aggr_tiers": [],
    "enable_harvest": False, "harvest_window_ticks": 200,
    "harvest_z_thresh": 1.0, "harvest_take_size": 30,
}

VFE_DELTA_VEV_5000 = {
    "symbol": "VEV_5000", "strike": 5000, "limit": 300,
    "soft_cap": 200, "soft_cap_max": 250, "offset": 8,
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
    within_day = state.timestamp % 1_000_000
    decay_t = min(1.0, max(0.0, within_day / cfg["decay_end_tick"]))
    hc_pct_now = (cfg["hard_cap_pct"]
                  + (cfg["hard_cap_end_pct"] - cfg["hard_cap_pct"]) * decay_t)
    hard_cap = hc_pct_now * limit
    aggr_max_now = int(round(cfg["aggr_max_take"]
                             + (cfg["aggr_max_take_end"] - cfg["aggr_max_take"]) * decay_t))

    out, bv, sv = [], 0, 0

    if cfg.get("enable_unwind", True):
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
    if cfg.get("enable_primary", True) and conv > 0 and direction != 0:
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
    if (cfg.get("enable_vfe_spillover", True)
            and cfg["prefix"] == "vfe" and conv > 0 and overflow > 0 and direction != 0):
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

    if abs(pos) < hard_cap:
        aggr_orders, abv, asv = _aggressive_mr_take(
            depth, sym, cfg, td, pos, bv, sv, aggr_max_now,
        )
        if aggr_orders:
            out.extend(aggr_orders); bv += abv; sv += asv

    p = cfg["prefix"]
    ttl = td.get(f"_{p}_harvest_ttl", 0)
    if ttl > 0:
        td[f"_{p}_harvest_ttl"] = ttl - 1

    if cfg.get("enable_harvest", False) and ttl > 0:
        sd_h = cfg["stdev_init"]
        if abs((mid - fair) / sd_h) < cfg["harvest_z_thresh"]:
            aggr_dir = td.get(f"_{p}_aggr_dir", 0)
            pos_now = pos + bv - sv
            harv_size = int(cfg.get("harvest_take_size", 30))
            if aggr_dir > 0 and pos_now > 0:
                qty = min(pos_now, harv_size, limit + pos - sv)
                if qty > 0:
                    takes, filled = _walk_book(depth, -1, sym, lambda px: True, qty)
                    out.extend(takes); sv += filled
            elif aggr_dir < 0 and pos_now < 0:
                qty = min(-pos_now, harv_size, limit - pos - bv)
                if qty > 0:
                    takes, filled = _walk_book(depth, +1, sym, lambda px: True, qty)
                    out.extend(takes); bv += filled

    if cfg.get("enable_mm", True):
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
# Mark-flow overlay — runs AFTER all existing pipelines, augments orders
# ============================================================================
#
# Direction encoding:
#   informed:        buy → up, sell → down  (follow them)
#   fade:            buy → down, sell → up  (mirror them)
#   fade_sell_only:  sell → up only         (only their sell flow is mispriced)
#
# Per tick, scan state.market_trades[sym] for marks listed below; whichever
# direction has the freshest TTL wins. While a TTL is active, take across
# the spread up to take_size against remaining capacity (bv/sv supplied by
# caller after existing pipelines).

MF_LIMITS = {
    "HYDROGEL_PACK": 200,
    "VELVETFRUIT_EXTRACT": 200,
    "VEV_5300": 300, "VEV_5400": 300, "VEV_5500": 300,
}

MF_CFG = {
    "HYDROGEL_PACK": {
        "informed": ("Mark 14",),
        "fade":     ("Mark 38",),
        "ttl": 250, "take_size": 25,
    },
    "VELVETFRUIT_EXTRACT": {
        "informed": ("Mark 14",),
        "fade":     ("Mark 55",),
        "ttl": 250, "take_size": 20,
    },
    "VEV_5300": {"fade_sell_only": ("Mark 22",), "ttl": 250, "take_size": 12},
    "VEV_5400": {"fade_sell_only": ("Mark 22",), "ttl": 250, "take_size": 12},
    "VEV_5500": {"fade_sell_only": ("Mark 22",), "ttl": 250, "take_size": 12},
}


def _mark_flow_takes(state, store, existing_orders):
    out = {}
    mf = store.setdefault("_mf", {})

    for sym, cfg in MF_CFG.items():
        depth = state.order_depths.get(sym)
        if not depth or not depth.buy_orders or not depth.sell_orders:
            continue
        st = mf.setdefault(sym, {"up": 0, "down": 0})
        st["up"] = max(0, st["up"] - 1)
        st["down"] = max(0, st["down"] - 1)

        ttl = cfg["ttl"]
        for t in state.market_trades.get(sym, []) or []:
            buyer = getattr(t, "buyer", "") or ""
            seller = getattr(t, "seller", "") or ""
            if buyer in cfg.get("informed", ()):
                st["up"] = ttl
            if seller in cfg.get("informed", ()):
                st["down"] = ttl
            if buyer in cfg.get("fade", ()):
                st["down"] = ttl
            if seller in cfg.get("fade", ()):
                st["up"] = ttl
            if seller in cfg.get("fade_sell_only", ()):
                st["up"] = ttl

        if st["up"] == 0 and st["down"] == 0:
            continue
        # Most-recent (highest TTL remaining) signal wins
        direction = +1 if st["up"] >= st["down"] else -1
        if (direction > 0 and st["up"] == 0) or (direction < 0 and st["down"] == 0):
            continue

        existing = existing_orders.get(sym, [])
        bv = sum(o.quantity for o in existing if o.quantity > 0)
        sv = sum(-o.quantity for o in existing if o.quantity < 0)

        pos = state.position.get(sym, 0)
        limit = MF_LIMITS[sym]
        # Hard guard: don't blow position
        if abs(pos) >= int(0.95 * limit):
            continue

        take_size = cfg["take_size"]
        new_orders = []
        if direction > 0:
            room = max(0, min(take_size, limit - pos - bv))
            if room > 0:
                takes, _ = _walk_book(depth, +1, sym, lambda px: True, room)
                new_orders.extend(takes)
        else:
            room = max(0, min(take_size, limit + pos - sv))
            if room > 0:
                takes, _ = _walk_book(depth, -1, sym, lambda px: True, room)
                new_orders.extend(takes)

        if new_orders:
            out[sym] = new_orders

    return out


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

        mf_extra = _mark_flow_takes(state, store, orders)
        for sym, ors in mf_extra.items():
            orders.setdefault(sym, []).extend(ors)

        return orders, 0, json.dumps(store)
