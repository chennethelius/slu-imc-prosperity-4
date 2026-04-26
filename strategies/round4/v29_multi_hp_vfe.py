"""Round 4 v29 — multi-product: HP (v27) + VFE conviction MR with VFE Marks.

User directive: introduce other assets, leverage counterparty information.

Architecture:
  HP module: v27 conviction-weighted MR with dynamic +/-200 cap (untouched).
  VFE module: same conviction architecture, VFE-specific Marks, tighter Z.

VFE per-Mark classification (R3+R4, 5000ms forward-return analysis):
   Mark 67 informed-follow  fwd_b +1.92    weight +1.5  (ONE-SIDED buyer, n=165)
   Mark 49 noise-fade       fwd_s +1.99    weight -1.5  (mostly seller, n=122)
   Mark 22 noise-fade       fwd_s +1.87    weight -1.0  (mostly seller, n=126)
   Mark 14 noise-fade       fwd_s +0.82    weight -0.5  (two-way, n=647) flips role
   Mark 55 weak follow      fwd_b +0.39    weight +0.5  (two-way, n=1198)
   Mark 01 neutral                          weight  0   (n=504)

VFE_FAIR = 5249 (combined R3+R4 mean = 5248.87, median = 5248.5).
VFE_STDEV_INIT = 17 (vs HP's 33).
"""

import json
from typing import Any

from datamodel import (
    Listing, Observation, Order, OrderDepth, ProsperityEncoder, Symbol, Trade, TradingState,
)


class Logger:
    def __init__(self) -> None:
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects: Any, sep: str = " ", end: str = "\n") -> None:
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state, orders, conversions, trader_data):
        base = len(self.to_json([self.compress_state(state, ""),
                                 self.compress_orders(orders), conversions, "", ""]))
        m = (self.max_log_length - base) // 3
        print(self.to_json([self.compress_state(state, self.truncate(state.traderData, m)),
                            self.compress_orders(orders), conversions,
                            self.truncate(trader_data, m), self.truncate(self.logs, m)]))
        self.logs = ""

    def compress_state(self, s, td):
        return [s.timestamp, td, self.compress_listings(s.listings),
                self.compress_order_depths(s.order_depths),
                self.compress_trades(s.own_trades),
                self.compress_trades(s.market_trades),
                s.position, self.compress_observations(s.observations)]

    def compress_listings(self, ls):
        return [[l.symbol, l.product, l.denomination] for l in ls.values()]

    def compress_order_depths(self, ods):
        return {s: [od.buy_orders, od.sell_orders] for s, od in ods.items()}

    def compress_trades(self, trades):
        return [[t.symbol, t.price, t.quantity, t.buyer, t.seller, t.timestamp]
                for arr in trades.values() for t in arr]

    def compress_observations(self, obs):
        co = {p: [o.bidPrice, o.askPrice, o.transportFees, o.exportTariff, o.importTariff]
              for p, o in obs.conversionObservations.items()}
        return [obs.plainValueObservations, co]

    def compress_orders(self, orders):
        return [[o.symbol, o.price, o.quantity] for arr in orders.values() for o in arr]

    def to_json(self, v):
        return json.dumps(v, cls=ProsperityEncoder, separators=(",", ":"))

    def truncate(self, value, max_length):
        lo, hi = 0, min(len(value), max_length)
        out = ""
        while lo <= hi:
            mid = (lo + hi) // 2
            cand = value[:mid] + ("..." if mid < len(value) else "")
            if len(json.dumps(cand)) <= max_length:
                out = cand
                lo = mid + 1
            else:
                hi = mid - 1
        return out


logger = Logger()


# === Per-product configurations =============================================
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
        "Mark 67": +1.5,
        "Mark 49": -1.5,
        "Mark 22": -1.0,
        "Mark 14": -0.5,
        "Mark 55": +0.5,
    },
}


def conviction_orders(depth, pos, td, market_trades, cfg):
    """Generic conviction-weighted MR engine for a single product."""
    if not depth.buy_orders or not depth.sell_orders:
        return []
    bb = max(depth.buy_orders)
    ba = min(depth.sell_orders)
    mid = (bb + ba) / 2.0

    P = cfg["prefix"]
    sym = cfg["symbol"]
    LIM = cfg["limit"]
    fair = cfg["fair"]

    # Z-score
    dev = mid - fair
    var_key = f"_{P}_var"
    var = td.get(var_key, cfg["stdev_init"] ** 2)
    var = (1.0 - cfg["var_alpha"]) * var + cfg["var_alpha"] * (dev * dev)
    td[var_key] = var
    stdev = max(cfg["stdev_init"] * 0.15, var ** 0.5)
    z = dev / stdev
    abs_z = abs(z)
    z_str = 0.0 if abs_z < cfg["z_min"] else min(1.0, (abs_z - cfg["z_min"]) / (cfg["z_max"] - cfg["z_min"]))
    direction = +1 if dev < 0 else -1

    # EMA confluence
    ef_key, es_key, evs_key = f"_{P}_ef", f"_{P}_es", f"_{P}_evs"
    ema_f = td.get(ef_key, mid)
    ema_s = td.get(es_key, mid)
    ema_vs = td.get(evs_key, mid)
    ema_f = cfg["ema_fast"] * mid + (1.0 - cfg["ema_fast"]) * ema_f
    ema_s = cfg["ema_slow"] * mid + (1.0 - cfg["ema_slow"]) * ema_s
    ema_vs = cfg["ema_vslow"] * mid + (1.0 - cfg["ema_vslow"]) * ema_vs
    td[ef_key] = ema_f
    td[es_key] = ema_s
    td[evs_key] = ema_vs

    short = ema_f - ema_s
    medium = ema_s - ema_vs
    short_sign = 1 if short > 0 else (-1 if short < 0 else 0)
    medium_sign = 1 if medium > 0 else (-1 if medium < 0 else 0)
    if short_sign != 0 and short_sign == medium_sign and short_sign == direction:
        ema_str = min(1.0, abs(short) / cfg["ema_full"])
    else:
        ema_str = 0.0

    # Per-Mark weighted informed flow
    net_inf = 0.0
    if market_trades:
        weights = cfg["mark_weights"]
        for t in market_trades[-cfg["informed_lookback"]:]:
            buyer = (t.buyer or "")
            seller = (t.seller or "")
            qty = int(t.quantity)
            wb = weights.get(buyer, 0.0)
            ws = weights.get(seller, 0.0)
            net_inf += wb * qty
            net_inf -= ws * qty
    inf_sign = 1 if net_inf > 0 else (-1 if net_inf < 0 else 0)
    if inf_sign != 0 and inf_sign == direction:
        inf_str = min(1.0, abs(net_inf) / cfg["informed_full"])
    else:
        inf_str = 0.0

    if z_str == 0.0:
        conviction = 0.0
    else:
        conviction = cfg["w_z"] * z_str + cfg["w_ema"] * ema_str + cfg["w_inf"] * inf_str

    out = []
    bv = sv = 0

    # Hard-cap unwind
    cap_lots = cfg["hard_cap_pct"] * LIM
    if pos > cap_lots:
        for bid in sorted(depth.buy_orders, reverse=True):
            if bid < fair - 2:
                break
            avail = depth.buy_orders[bid]
            qty = min(avail, pos, LIM + pos - sv)
            if qty <= 0: break
            out.append(Order(sym, bid, -qty)); sv += qty
            if pos + bv - sv <= cap_lots * 0.5: break
    elif pos < -cap_lots:
        for ask in sorted(depth.sell_orders):
            if ask > fair + 2:
                break
            avail = -depth.sell_orders[ask]
            qty = min(avail, -pos, LIM - pos - bv)
            if qty <= 0: break
            out.append(Order(sym, ask, qty)); bv += qty
            if pos + bv - sv >= -cap_lots * 0.5: break

    # Conviction take with dynamic cap
    pos_after = pos + bv - sv
    dyn_cap_pct = cfg["base_cap_pct"] + (cfg["full_cap_pct"] - cfg["base_cap_pct"]) * conviction
    soft_cap = dyn_cap_pct * LIM

    if conviction > 0:
        target_qty = int(round(cfg["take_max"] * conviction))
        if direction > 0 and pos_after < soft_cap:
            max_pay = fair + cfg["take_offset"]
            remaining = target_qty
            for ask in sorted(depth.sell_orders):
                if ask > max_pay or remaining <= 0:
                    break
                avail = -depth.sell_orders[ask]
                room = LIM - pos - bv
                cap_room = int(soft_cap - pos_after)
                qty = min(avail, room, cap_room, remaining)
                if qty <= 0: break
                out.append(Order(sym, ask, qty)); bv += qty
                remaining -= qty
                pos_after = pos + bv - sv
        elif direction < 0 and pos_after > -soft_cap:
            min_recv = fair - cfg["take_offset"]
            remaining = target_qty
            for bid in sorted(depth.buy_orders, reverse=True):
                if bid < min_recv or remaining <= 0:
                    break
                avail = depth.buy_orders[bid]
                room = LIM + pos - sv
                cap_room = int(soft_cap + pos_after)
                qty = min(avail, room, cap_room, remaining)
                if qty <= 0: break
                out.append(Order(sym, bid, -qty)); sv += qty
                remaining -= qty
                pos_after = pos + bv - sv

    # Make leg (always-on, asymmetric pull-to-zero, MR boost when mid drifts)
    pos_after = pos + bv - sv
    mr_dir = 0
    if mid < fair - cfg["mr_thresh"]:
        mr_dir = +1
    elif mid > fair + cfg["mr_thresh"]:
        mr_dir = -1

    bid_px = min(bb + 1, fair - 1)
    ask_px = max(ba - 1, fair + 1)

    ratio = pos_after / LIM
    buy_mult = max(0.0, 1.0 - cfg["flat_pull"] * ratio)
    sell_mult = max(0.0, 1.0 + cfg["flat_pull"] * ratio)
    if mr_dir > 0:
        buy_mult *= cfg["mr_boost"]
    elif mr_dir < 0:
        sell_mult *= cfg["mr_boost"]

    buy_q = max(0, int(round(cfg["qsize"] * buy_mult)))
    sell_q = max(0, int(round(cfg["qsize"] * sell_mult)))
    buy_q = max(0, min(buy_q, LIM - pos - bv))
    sell_q = max(0, min(sell_q, LIM + pos - sv))

    if bid_px < ask_px:
        if buy_q > 0:
            out.append(Order(sym, int(bid_px), buy_q))
        if sell_q > 0:
            out.append(Order(sym, int(ask_px), -sell_q))

    return out


class Trader:
    def bid(self):
        return 0

    def run(self, state: TradingState):
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}

        orders: dict[str, list[Order]] = {}

        for cfg in (HP_CFG, VFE_CFG):
            sym = cfg["symbol"]
            depth = state.order_depths.get(sym)
            if depth:
                pos = state.position.get(sym, 0)
                trades = state.market_trades.get(sym, [])
                ors = conviction_orders(depth, pos, td, trades, cfg)
                if ors:
                    orders[sym] = ors

        trader_data = json.dumps(td)
        logger.flush(state, orders, 0, trader_data)
        return orders, 0, trader_data
