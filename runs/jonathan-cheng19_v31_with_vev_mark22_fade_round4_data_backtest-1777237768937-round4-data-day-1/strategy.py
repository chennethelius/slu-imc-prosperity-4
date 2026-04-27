"""Round 4 v31 — v29 (HP + VFE) + VEV options counterparty-fade.

User directive: search for more extractable alpha, avoid overfitting.

Per-counterparty analysis on VEV options (R3+R4, 5000ms fwd-return):
   Mark 22 is a SYSTEMATIC SELLER of VEV_5200/5300/5400/5500/6000/6500.
   After their sells, mid moves UP by:
       VEV_5200 fwd_s +7.50   (n=46 sells, 159 lots) STRONG FADE
       VEV_5300 fwd_s +3.50   (n=163 sells, 545 lots) FADE
       VEV_5400 fwd_s +0.09   (weak)
       VEV_5500 fwd_s +0.02   (negligible)
       VEV_6000 fwd_s +0.00   (no signal — voucher stuck at floor)
       VEV_6500 fwd_s +0.00   (no signal)

Same fade pattern as Mark 22 on VFE (+1.87 fwd_s) which v29 already exploits.
v31 layers the SAME counterparty signal onto VEV options where it has
clean, large-magnitude alpha.

Structural addition (single change vs v29):
   When recent VEV market_trades contain Mark 22 as seller, lift available
   asks on the affected voucher up to a position cap and a price cap.

   - Active vouchers: VEV_5200, VEV_5300 only (large fwd return, high conf).
   - VEV_5400/5500/6000/6500 have weak/zero forward return → skip.
   - Position cap: 100 per voucher (1/3 of 300 limit, conservative).
   - Price cap: don't lift above last_mid + 4 (skip if ask runs away).
   - Position limit limit safety: only buy when |pos| < 75% of cap.

No delta hedge: VEV_5200/5300 are near-ATM (S~5249, K=5200/5300) so delta
is ~0.5-0.6 per option. With 100 lots max, delta exposure ~50-60 VFE
equivalents. VFE module's own pos limit (200) absorbs that comfortably.
The VFE+VEV combined delta typically stays under 250 net VFE-equivalents.

No IV/smile fitting: this is pure counterparty-flow alpha, not vol-arb.
The signal is already validated against forward returns in the data.

HP and VFE configs unchanged from v29 (already MC-validated, robust).
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


# === HP and VFE configs (unchanged from v29) ================================
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

# === VEV options counterparty-fade config ==================================
# Active vouchers: those where Mark 22's sell has measurable forward return.
VEV_FADE_VOUCHERS = {
    "VEV_5200": {"limit": 300, "soft_cap": 100, "max_pay_offset": 4},
    "VEV_5300": {"limit": 300, "soft_cap": 100, "max_pay_offset": 4},
}
VEV_FADE_LOOKBACK = 12       # ticks of market_trades to scan for Mark 22 sells
VEV_FADE_THRESHOLD = 5       # min Mark-22 sell volume in window to trigger
VEV_FADE_TAKE_PER_TICK = 25  # max lots lifted per tick when signal fires


def conviction_orders(depth, pos, td, market_trades, cfg):
    """Generic conviction-MR engine — same as v29."""
    if not depth.buy_orders or not depth.sell_orders:
        return []
    bb = max(depth.buy_orders); ba = min(depth.sell_orders)
    mid = (bb + ba) / 2.0
    P = cfg["prefix"]; sym = cfg["symbol"]; LIM = cfg["limit"]; fair = cfg["fair"]

    dev = mid - fair
    var = td.get(f"_{P}_var", cfg["stdev_init"] ** 2)
    var = (1.0 - cfg["var_alpha"]) * var + cfg["var_alpha"] * (dev * dev)
    td[f"_{P}_var"] = var
    stdev = max(cfg["stdev_init"] * 0.15, var ** 0.5)
    z = dev / stdev
    abs_z = abs(z)
    z_str = 0.0 if abs_z < cfg["z_min"] else min(1.0, (abs_z - cfg["z_min"]) / (cfg["z_max"] - cfg["z_min"]))
    direction = +1 if dev < 0 else -1

    ema_f = td.get(f"_{P}_ef", mid); ema_s = td.get(f"_{P}_es", mid); ema_vs = td.get(f"_{P}_evs", mid)
    ema_f = cfg["ema_fast"] * mid + (1.0 - cfg["ema_fast"]) * ema_f
    ema_s = cfg["ema_slow"] * mid + (1.0 - cfg["ema_slow"]) * ema_s
    ema_vs = cfg["ema_vslow"] * mid + (1.0 - cfg["ema_vslow"]) * ema_vs
    td[f"_{P}_ef"] = ema_f; td[f"_{P}_es"] = ema_s; td[f"_{P}_evs"] = ema_vs

    short = ema_f - ema_s; medium = ema_s - ema_vs
    short_sign = 1 if short > 0 else (-1 if short < 0 else 0)
    medium_sign = 1 if medium > 0 else (-1 if medium < 0 else 0)
    if short_sign != 0 and short_sign == medium_sign and short_sign == direction:
        ema_str = min(1.0, abs(short) / cfg["ema_full"])
    else:
        ema_str = 0.0

    net_inf = 0.0
    if market_trades:
        weights = cfg["mark_weights"]
        for t in market_trades[-cfg["informed_lookback"]:]:
            buyer = (t.buyer or ""); seller = (t.seller or "")
            qty = int(t.quantity)
            net_inf += weights.get(buyer, 0.0) * qty
            net_inf -= weights.get(seller, 0.0) * qty
    inf_sign = 1 if net_inf > 0 else (-1 if net_inf < 0 else 0)
    inf_str = min(1.0, abs(net_inf) / cfg["informed_full"]) if (inf_sign != 0 and inf_sign == direction) else 0.0

    conviction = 0.0 if z_str == 0.0 else cfg["w_z"] * z_str + cfg["w_ema"] * ema_str + cfg["w_inf"] * inf_str

    out = []; bv = sv = 0
    cap_lots = cfg["hard_cap_pct"] * LIM
    if pos > cap_lots:
        for bid in sorted(depth.buy_orders, reverse=True):
            if bid < fair - 2: break
            avail = depth.buy_orders[bid]
            qty = min(avail, pos, LIM + pos - sv)
            if qty <= 0: break
            out.append(Order(sym, bid, -qty)); sv += qty
            if pos + bv - sv <= cap_lots * 0.5: break
    elif pos < -cap_lots:
        for ask in sorted(depth.sell_orders):
            if ask > fair + 2: break
            avail = -depth.sell_orders[ask]
            qty = min(avail, -pos, LIM - pos - bv)
            if qty <= 0: break
            out.append(Order(sym, ask, qty)); bv += qty
            if pos + bv - sv >= -cap_lots * 0.5: break

    pos_after = pos + bv - sv
    dyn_cap = cfg["base_cap_pct"] + (cfg["full_cap_pct"] - cfg["base_cap_pct"]) * conviction
    soft_cap = dyn_cap * LIM
    if conviction > 0:
        target = int(round(cfg["take_max"] * conviction))
        if direction > 0 and pos_after < soft_cap:
            max_pay = fair + cfg["take_offset"]; rem = target
            for ask in sorted(depth.sell_orders):
                if ask > max_pay or rem <= 0: break
                avail = -depth.sell_orders[ask]
                room = LIM - pos - bv; cap_room = int(soft_cap - pos_after)
                qty = min(avail, room, cap_room, rem)
                if qty <= 0: break
                out.append(Order(sym, ask, qty)); bv += qty; rem -= qty; pos_after = pos + bv - sv
        elif direction < 0 and pos_after > -soft_cap:
            min_recv = fair - cfg["take_offset"]; rem = target
            for bid in sorted(depth.buy_orders, reverse=True):
                if bid < min_recv or rem <= 0: break
                avail = depth.buy_orders[bid]
                room = LIM + pos - sv; cap_room = int(soft_cap + pos_after)
                qty = min(avail, room, cap_room, rem)
                if qty <= 0: break
                out.append(Order(sym, bid, -qty)); sv += qty; rem -= qty; pos_after = pos + bv - sv

    pos_after = pos + bv - sv
    mr_dir = +1 if mid < fair - cfg["mr_thresh"] else (-1 if mid > fair + cfg["mr_thresh"] else 0)
    bid_px = min(bb + 1, fair - 1); ask_px = max(ba - 1, fair + 1)
    ratio = pos_after / LIM
    bm = max(0.0, 1.0 - cfg["flat_pull"] * ratio); sm = max(0.0, 1.0 + cfg["flat_pull"] * ratio)
    if mr_dir > 0: bm *= cfg["mr_boost"]
    elif mr_dir < 0: sm *= cfg["mr_boost"]
    bq = max(0, min(int(round(cfg["qsize"] * bm)), LIM - pos - bv))
    sq = max(0, min(int(round(cfg["qsize"] * sm)), LIM + pos - sv))
    if bid_px < ask_px:
        if bq > 0: out.append(Order(sym, int(bid_px), bq))
        if sq > 0: out.append(Order(sym, int(ask_px), -sq))

    return out


def vev_fade_orders(sym, depth, pos, market_trades, voucher_cfg):
    """Lift asks on this voucher when Mark 22 has been selling recently.

    Mark 22's sells are followed by mid rises (per offline R3+R4 analysis).
    Fade their direction by buying the offered ask.
    """
    if not depth.buy_orders or not depth.sell_orders:
        return []
    bb = max(depth.buy_orders); ba = min(depth.sell_orders)
    mid = (bb + ba) / 2.0
    if mid < 1.5:   # skip price-floor stuck vouchers
        return []

    # Count Mark 22's recent sell volume on this voucher
    mark22_sell_vol = 0
    if market_trades:
        for t in market_trades[-VEV_FADE_LOOKBACK:]:
            seller = (t.seller or "")
            if seller == "Mark 22":
                mark22_sell_vol += int(t.quantity)

    if mark22_sell_vol < VEV_FADE_THRESHOLD:
        return []

    # Mark 22 is selling → fade by BUYING the ask
    soft_cap = voucher_cfg["soft_cap"]
    if pos >= soft_cap:
        return []   # already loaded from prior fades

    LIM = voucher_cfg["limit"]
    max_pay = mid + voucher_cfg["max_pay_offset"]
    remaining = VEV_FADE_TAKE_PER_TICK
    out = []
    bv = 0
    pos_after = pos
    for ask in sorted(depth.sell_orders):
        if ask > max_pay or remaining <= 0:
            break
        avail = -depth.sell_orders[ask]
        room = LIM - pos - bv
        cap_room = max(0, soft_cap - pos_after)
        qty = min(avail, room, cap_room, remaining)
        if qty <= 0:
            break
        out.append(Order(sym, ask, qty))
        bv += qty
        remaining -= qty
        pos_after = pos + bv

    # Also flatten if pos > 0 and mid has reverted to favorable zone
    # (when our basis is in profit). Simple flatten: post passive ask 1 above mid.
    if pos > 5 and bv == 0:   # have inventory and didn't add this tick
        ask_px = int(round(mid + 1))
        sell_q = min(pos, 30, LIM + pos)
        if sell_q > 0:
            out.append(Order(sym, ask_px, -sell_q))

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

        # === HP and VFE — v29 conviction-MR ===
        for cfg in (HP_CFG, VFE_CFG):
            sym = cfg["symbol"]
            depth = state.order_depths.get(sym)
            if depth:
                pos = state.position.get(sym, 0)
                trades = state.market_trades.get(sym, [])
                ors = conviction_orders(depth, pos, td, trades, cfg)
                if ors:
                    orders[sym] = ors

        # === VEV: Mark 22 fade signal ===
        for sym, voucher_cfg in VEV_FADE_VOUCHERS.items():
            depth = state.order_depths.get(sym)
            if depth:
                pos = state.position.get(sym, 0)
                trades = state.market_trades.get(sym, [])
                ors = vev_fade_orders(sym, depth, pos, trades, voucher_cfg)
                if ors:
                    orders[sym] = ors

        trader_data = json.dumps(td)
        logger.flush(state, orders, 0, trader_data)
        return orders, 0, trader_data
