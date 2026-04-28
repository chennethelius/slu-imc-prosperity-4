"""
Round 4 — z_take_hybrid (TMP): rollbias_selective + tuned no_marks for HP/VEV_5200.

Per-asset PnL audit (round4 d1-d3, QP=1.0) showed no_marks beats z_take by
+$25k on HP and +$15k on VEV_5200 (3-day sums) but loses on every other
asset. The hybrid uses each strategy's strength per-product:

  HP, VEV_5200: no_marks conviction / anchor logic
  VFE, VEV_4000-5500 (excl. 5200): z_take_rollbias_selective

But raw no_marks logic is too aggressive — d2 (the FLAT day, HP mid drift
only −$1.82) was getting whipsawed, dragging per-day min from baseline 205k
to 190k. Tuning HP take_max 80→25 and VEV_5200 DIVERGE_TAKE_SIZE 30→12
caps the d2 downside while preserving most of d1/d3 upside.

Final config (HP take_max=25, VEV_5200 DIV_SIZE=12):
                       baseline     rb_selective   z_take_hybrid
  d1                    261,612       263,925        283,659  (+22,047)
  d2                    205,530       205,736        200,039  (-5,491)
  d3                    266,827       273,651        291,414  (+24,587)
  mean                  244,656       247,771        258,370  (+13,714)
  min                   205,530       205,736        200,039  (-5,491)
  mean+min              450,186       453,507        458,409  (+8,223)
  3-day sum             733,969       743,312        775,111  (+41,142)

Mean+min is +0.91% better than rb_selective and +1.83% over baseline.
Note: not strict Pareto — min drops -$5,491 vs baseline. The trade is
+$13.7k mean for −$5.5k min, mean+min +$8.2k. By the team's mean+min
ranking rule this is the new winner; by strict Pareto over baseline,
rb_selective ($453,507 / +0.74%) remains preferable.

Per-asset deltas vs baseline (3-day sum):
  HP                +20,131 ★    no_marks conviction (take_max=25)
  VEV_5200          +11,668 ★    no_marks anchor (DIV_SIZE=12)
  VEV_4500           +4,783       rollbias k=0.95
  VEV_5300           +2,109       rollbias k=0.95
  VFE                +2,057       rollbias k=0.95
  VEV_4000             +394       rollbias k=0.95
  TOTAL             +41,142

Tuning sweep map (each cell is mean+min):
  HP_take  V52_div=10  V52_div=12  V52_div=15
  20       455,982     457,466     456,421
  25       456,925    458,409★    457,365
  30       456,191     457,676     456,632
  35       455,970     457,455     456,411
  40       454,765     456,250     455,205
"""

import json
from typing import Any
from datamodel import Listing, Observation, Order, OrderDepth, ProsperityEncoder, Symbol, Trade, TradingState



class Logger:
    def __init__(self) -> None:
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects: Any, sep: str = " ", end: str = "\n") -> None:
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state: TradingState, orders: dict[Symbol, list[Order]], conversions: int, trader_data: str) -> None:
        base_length = len(self.to_json([self.compress_state(state, ""), self.compress_orders(orders), conversions, "", ""]))
        max_item_length = (self.max_log_length - base_length) // 3
        print(self.to_json([
            self.compress_state(state, self.truncate(state.traderData, max_item_length)),
            self.compress_orders(orders),
            conversions,
            self.truncate(trader_data, max_item_length),
            self.truncate(self.logs, max_item_length),
        ]))
        self.logs = ""

    def compress_state(self, state: TradingState, trader_data: str) -> list[Any]:
        return [state.timestamp, trader_data, self.compress_listings(state.listings),
                self.compress_order_depths(state.order_depths), self.compress_trades(state.own_trades),
                self.compress_trades(state.market_trades), state.position, self.compress_observations(state.observations)]

    def compress_listings(self, listings):
        return [[l.symbol, l.product, l.denomination] for l in listings.values()]

    def compress_order_depths(self, order_depths):
        return {s: [od.buy_orders, od.sell_orders] for s, od in order_depths.items()}

    def compress_trades(self, trades):
        return [[t.symbol, t.price, t.quantity, t.buyer, t.seller, t.timestamp]
                for arr in trades.values() for t in arr]

    def compress_observations(self, observations: Observation) -> list[Any]:
        conversion_observations = {}
        for product, obs in observations.conversionObservations.items():
            conversion_observations[product] = [
                obs.bidPrice, obs.askPrice, obs.transportFees,
                obs.exportTariff, obs.importTariff, obs.sugarPrice, obs.sunlightIndex,
            ]
        return [observations.plainValueObservations, conversion_observations]

    def compress_orders(self, orders):
        return [[o.symbol, o.price, o.quantity] for arr in orders.values() for o in arr]

    def to_json(self, value: Any) -> str:
        return json.dumps(value, cls=ProsperityEncoder, separators=(",", ":"))

    def truncate(self, value: str, max_length: int) -> str:
        lo, hi = 0, min(len(value), max_length)
        out = ""
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = value[:mid]
            if len(candidate) < len(value):
                candidate += "..."
            if len(json.dumps(candidate)) <= max_length:
                out = candidate; lo = mid + 1
            else:
                hi = mid - 1
        return out


logger = Logger()

# ============================================================================
# Per-product config
# ============================================================================

# Rolling EWMA parameters. ALPHA=0.004 → ~500-tick half-life. WARMUP
# suppresses trading for the first WARMUP ticks so the EWMA stabilises.
# mean_init / sd_init seed the EWMA with the historical static values
# (kept for reference; once warmed up they stop mattering).
DEFAULT_ALPHA = 0.0025
DEFAULT_WARMUP = 0
DEFAULT_K_DEFAULT = 1.0    # used when rollbias_on is False — k=1 = exact static
DEFAULT_K_ROLLBIAS = 0.95  # used when rollbias_on is True
DEFAULT_K_IMB = 0.75       # imbalance bias strength (only when imb_on)

# Per-product gating from the 3-day asset breakdown:
#   rollbias_on=True only where it net helps over baseline:
#     VFE (+2057), VEV_4000 (+394), VEV_4500 (+4783), VEV_5300 (+2109)
#   rollbias_on=False where it net hurts:
#     HP (-235), VEV_5000 (-472), VEV_5100 (-121), VEV_5200 (-54),
#     VEV_5400 (0), VEV_5500 (-2316)
#   imb_on=True only on VFE (+1212); HP/VEV_4000 imb adds nothing useful.
CFGS = [
    # HP and VEV_5200 are handled by no_marks's conviction / anchor logic below.
    {"symbol": "VELVETFRUIT_EXTRACT", "static_mean": 5247, "static_sd": 17.091, "z_thresh": 1.0, "take_size": 17, "limit": 200, "rollbias_on": True,  "imb_on": False},
    {"symbol": "VEV_4000",            "static_mean": 1247, "static_sd": 17.114, "z_thresh": 1.0, "take_size": 17, "limit": 300, "rollbias_on": True,  "imb_on": False},
    {"symbol": "VEV_4500",            "static_mean":  747, "static_sd": 17.105, "z_thresh": 1.0, "take_size": 17, "limit": 300, "rollbias_on": True,  "imb_on": False},
    {"symbol": "VEV_5000",            "static_mean":  252, "static_sd": 16.381, "z_thresh": 1.0, "take_size": 17, "limit": 300, "rollbias_on": False, "imb_on": False},
    {"symbol": "VEV_5100",            "static_mean":  163, "static_sd": 15.327, "z_thresh": 1.0, "take_size": 17, "limit": 300, "rollbias_on": False, "imb_on": False},
    {"symbol": "VEV_5300",            "static_mean":   43, "static_sd":  8.976, "z_thresh": 1.0, "take_size": 17, "limit": 300, "rollbias_on": True,  "imb_on": False},
    {"symbol": "VEV_5400",            "static_mean":   14, "static_sd":  4.608, "z_thresh": 1.0, "take_size": 17, "limit": 300, "rollbias_on": False, "imb_on": False},
    {"symbol": "VEV_5500",            "static_mean":    6, "static_sd":  2.477, "z_thresh": 1.0, "take_size": 17, "limit": 300, "rollbias_on": False, "imb_on": False},
]


# ============================================================================
# no_marks-derived layers — used ONLY for HYDROGEL_PACK and VEV_5200
# (per-asset audit showed +25k and +15k gains over z_take on these two)
# ============================================================================

import math

HP_CFG = {
    "prefix": "hp", "symbol": "HYDROGEL_PACK", "limit": 200, "fair": 10002,
    "stdev_init": 33.0, "var_alpha": 0.005,
    "qsize": 35, "flat_pull": 1.0, "mr_thresh": 4, "mr_boost": 1.5,
    "z_min": 0.7, "z_max": 2.0,
    "ema_fast": 0.30, "ema_slow": 0.05, "ema_vslow": 0.02, "ema_full": 1.5,
    "w_z": 0.625, "w_ema": 0.375,
    "take_max": 25, "take_offset": 4,
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

VEV_5200_CFG = {
    "product": "VEV_5200", "limit": 300, "qsize": 30, "diverge_threshold": 11,
    "regime_mode": "off", "use_vol_adjust": False,
}

ANCHOR_TAKE_WIDTH = 1
ANCHOR_WARMUP = 500
DIVERGE_TAKE_SIZE = 12
MAX_DIVERGE_POS = 295
MM_BOOST = 1.0


def _full_depth_mid(depth):
    bids = list(depth.buy_orders.items())
    asks = [(p, -v) for p, v in depth.sell_orders.items()]
    bv = sum(v for _, v in bids)
    av = sum(v for _, v in asks)
    if bv <= 0 or av <= 0:
        return (max(depth.buy_orders) + min(depth.sell_orders)) / 2
    return (sum(p * v for p, v in bids) / bv + sum(p * v for p, v in asks) / av) / 2


def _conviction_hp(depth, td, cfg):
    if not depth.buy_orders or not depth.sell_orders:
        return None
    bb = max(depth.buy_orders); ba = min(depth.sell_orders)
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
    short = ema_f - ema_s; medium = ema_s - ema_vs
    s_sign = (short > 0) - (short < 0)
    m_sign = (medium > 0) - (medium < 0)
    ema_str = (min(1.0, abs(short) / cfg["ema_full"])
               if s_sign != 0 and s_sign == m_sign == direction else 0.0)
    conv = (cfg["w_z"] * z_str + cfg["w_ema"] * ema_str if z_str > 0 else 0.0)
    return bb, ba, mid, direction, conv


def _aggressive_mr_take_hp(depth, sym, cfg, td, pos, bv_in, sv_in, max_take):
    if cfg["aggr_mean_mode"] == "off" or max_take <= 0:
        return [], 0, 0
    bb = max(depth.buy_orders); ba = min(depth.sell_orders)
    mid = (bb + ba) / 2.0
    p = cfg["prefix"]
    mean = cfg["fair"]
    sd = cfg["stdev_init"]  # static sd_source
    z = (mid - mean) / sd
    abs_z = abs(z)
    cap = max_take if abs_z >= cfg["aggr_z_thresh"] else 0
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
        return takes, filled, 0
    room = max(0, min(cap, limit + pos - sv_in))
    if room <= 0:
        return [], 0, 0
    takes, filled = _walk_book(depth, -1, sym, lambda px: px >= mean, room)
    return takes, 0, filled


def _hp_orders(state, cfg, td):
    sym = cfg["symbol"]
    depth = state.order_depths.get(sym)
    if depth is None:
        return []
    sig = _conviction_hp(depth, td, cfg)
    if sig is None:
        return []
    bb, ba, mid, direction, conv = sig
    pos = state.position.get(sym, 0)
    limit, fair = cfg["limit"], cfg["fair"]
    hard_cap = cfg["hard_cap_pct"] * limit
    aggr_max = cfg["aggr_max_take"]

    out, bv, sv = [], 0, 0

    # Unwind
    if pos > hard_cap:
        target = pos - int(hard_cap * 0.5)
        unw, filled = _walk_book(depth, -1, sym, lambda px: px >= fair - 2, target)
        out.extend(unw); sv += filled
    elif pos < -hard_cap:
        target = -pos - int(hard_cap * 0.5)
        unw, filled = _walk_book(depth, +1, sym, lambda px: px <= fair + 2, target)
        out.extend(unw); bv += filled

    # Primary
    primary_target = int(round(cfg["take_max"] * conv)) if conv > 0 else 0
    if conv > 0 and direction != 0:
        soft_cap = (cfg["base_cap_pct"]
                    + (cfg["full_cap_pct"] - cfg["base_cap_pct"]) * conv) * limit
        pos_after = pos + bv - sv
        offset = cfg["take_offset"]
        if direction > 0 and pos_after < soft_cap:
            qty = min(primary_target, limit - pos - bv, int(soft_cap - pos_after))
            takes, filled = _walk_book(depth, +1, sym, lambda px: px <= fair + offset, qty)
            out.extend(takes); bv += filled
        elif direction < 0 and pos_after > -soft_cap:
            qty = min(primary_target, limit + pos - sv, int(soft_cap + pos_after))
            takes, filled = _walk_book(depth, -1, sym, lambda px: px >= fair - offset, qty)
            out.extend(takes); sv += filled

    # Aggressive layer
    if abs(pos) < hard_cap:
        ao, abv, asv = _aggressive_mr_take_hp(depth, sym, cfg, td, pos, bv, sv, aggr_max)
        if ao:
            out.extend(ao); bv += abv; sv += asv

    # MM layer
    pos_after = pos + bv - sv
    mr_dir = (+1 if mid < fair - cfg["mr_thresh"]
              else -1 if mid > fair + cfg["mr_thresh"] else 0)
    bid_px = min(bb + 1, fair - 1)
    ask_px = max(ba - 1, fair + 1)
    ratio = pos_after / limit
    bm = max(0.0, 1.0 - cfg["flat_pull"] * ratio)
    sm = max(0.0, 1.0 + cfg["flat_pull"] * ratio)
    if mr_dir > 0: bm *= cfg["mr_boost"]
    elif mr_dir < 0: sm *= cfg["mr_boost"]
    if conv > 0:
        if direction > 0: bm *= 1.0 + MM_BOOST * conv
        elif direction < 0: sm *= 1.0 + MM_BOOST * conv
    bq = max(0, min(int(round(cfg["qsize"] * bm)), limit - pos - bv))
    sq = max(0, min(int(round(cfg["qsize"] * sm)), limit + pos - sv))
    if bid_px < ask_px:
        if bq > 0: out.append(Order(sym, int(bid_px), bq))
        if sq > 0: out.append(Order(sym, int(ask_px), -sq))
    return out


def _vev_5200_orders(state, cfg, scratch):
    sym = cfg["product"]
    depth = state.order_depths.get(sym)
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    bb = max(depth.buy_orders); ba = min(depth.sell_orders)
    mid = (bb + ba) / 2
    fair = _full_depth_mid(depth)
    pos = state.position.get(sym, 0)
    limit = cfg["limit"]

    n = scratch.get("anchor_n", 0) + 1
    s = scratch.get("anchor_sum", 0.0) + mid
    scratch["anchor_n"], scratch["anchor_sum"] = n, s
    anchor = s / n

    out, bv, sv = [], 0, 0
    threshold = cfg["diverge_threshold"]
    diverge = mid - anchor
    if threshold > 0 and n >= ANCHOR_WARMUP and abs(diverge) >= threshold:
        max_pos = MAX_DIVERGE_POS  # regime_mode=off, no scaling
        if diverge > 0 and pos > -max_pos:
            qty = min(DIVERGE_TAKE_SIZE, limit + pos, pos + max_pos)
            takes, filled = _walk_book(depth, -1, sym, lambda px: True, qty)
            out.extend(takes); sv += filled
        elif diverge < 0 and pos < max_pos:
            qty = min(DIVERGE_TAKE_SIZE, limit - pos, max_pos - pos)
            takes, filled = _walk_book(depth, +1, sym, lambda px: True, qty)
            out.extend(takes); bv += filled

    pos_eff = pos + bv - sv
    takes, filled = _walk_book(depth, +1, sym, lambda px: px < fair - ANCHOR_TAKE_WIDTH, limit - pos_eff)
    out.extend(takes); bv += filled
    takes, filled = _walk_book(depth, -1, sym, lambda px: px > fair + ANCHOR_TAKE_WIDTH, limit + pos_eff)
    out.extend(takes); sv += filled

    qsize = cfg["qsize"]
    bid_px = min(math.floor((fair + bb) / 2), ba - 1)
    ask_px = max(math.ceil((fair + ba) / 2), bb + 1)
    if bid_px < ask_px:
        bq = max(0, min(qsize, limit - pos - bv))
        sq = max(0, min(qsize, limit + pos - sv))
        if bq > 0: out.append(Order(sym, bid_px, bq))
        if sq > 0: out.append(Order(sym, ask_px, -sq))
    return out


def _imbalance(depth):
    bv = sum(abs(v) for v in depth.buy_orders.values())
    av = sum(abs(v) for v in depth.sell_orders.values())
    if bv + av <= 0:
        return 0.0
    return (bv - av) / (bv + av)


# ============================================================================
# Book walker — fill against the resting book on `side` at prices
# matching `ok(px)`, up to qty_target. side=+1 hits asks (buy); side=-1
# hits bids (sell).
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
# Per-product z-take
# ============================================================================

def _z_take_orders(state, cfg, store):
    sym = cfg["symbol"]
    depth = state.order_depths.get(sym)
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    mid = (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0

    static_mean = float(cfg["static_mean"])
    static_sd = float(cfg["static_sd"])

    # Rolling EWMA mean / variance, seeded from the static anchors.
    alpha = DEFAULT_ALPHA
    n = store.get(f"_{sym}_n", 0) + 1
    mean_prev = store.get(f"_{sym}_m", static_mean)
    var_prev = store.get(f"_{sym}_v", static_sd ** 2)
    dev = mid - mean_prev
    rolling_mean = (1.0 - alpha) * mean_prev + alpha * mid
    rolling_var = (1.0 - alpha) * var_prev + alpha * dev * dev
    store[f"_{sym}_n"] = n
    store[f"_{sym}_m"] = rolling_mean
    store[f"_{sym}_v"] = rolling_var

    if n < DEFAULT_WARMUP:
        return []

    rolling_sd = rolling_var ** 0.5
    if rolling_sd <= 0 or static_sd <= 0:
        return []

    # Per-product rollbias gating: k=1 (=static) where rollbias hurts,
    # k=0.95 where it helps. Imbalance bias only on VFE.
    k = DEFAULT_K_ROLLBIAS if cfg.get("rollbias_on") else DEFAULT_K_DEFAULT
    z_local = (mid - rolling_mean) / static_sd
    drift_z = (rolling_mean - static_mean) / static_sd
    z = z_local + k * drift_z
    if cfg.get("imb_on"):
        z = z + DEFAULT_K_IMB * _imbalance(depth)

    if abs(z) < cfg["z_thresh"]:
        return []

    pos = state.position.get(sym, 0)
    limit = cfg["limit"]
    take_size = cfg["take_size"]

    # Take filter uses STATIC mean — never enter at prices on the wrong
    # side of long-run fair regardless of where rolling drifted.
    if z > 0:
        room = max(0, min(take_size, limit + pos))
        if room <= 0:
            return []
        orders, _ = _walk_book(depth, -1, sym, lambda px: px >= static_mean, room)
        return orders

    room = max(0, min(take_size, limit - pos))
    if room <= 0:
        return []
    orders, _ = _walk_book(depth, +1, sym, lambda px: px <= static_mean, room)
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

        # z_take_rollbias_selective for VFE / VEV_4000-5500 (excluding 5200)
        for cfg in CFGS:
            ors = _z_take_orders(state, cfg, store)
            if ors:
                orders[cfg["symbol"]] = ors

        # no_marks conviction for HP
        hp_orders = _hp_orders(state, HP_CFG, store)
        if hp_orders:
            orders[HP_CFG["symbol"]] = hp_orders

        # no_marks anchor for VEV_5200
        scratch = store.setdefault(VEV_5200_CFG["product"], {})
        vev_5200_orders = _vev_5200_orders(state, VEV_5200_CFG, scratch)
        if vev_5200_orders:
            orders[VEV_5200_CFG["product"]] = vev_5200_orders

        return orders, 0, json.dumps(store)
