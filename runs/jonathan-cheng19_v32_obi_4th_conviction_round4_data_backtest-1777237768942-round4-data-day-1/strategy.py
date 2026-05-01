"""Round 4 v32 — v29 + Order-Book Imbalance as 4th conviction component.

Offline analysis (R4, 30k ticks per product) found a CLEAN CONTRARIAN signal:

   imb = (sum_bid_vol - sum_ask_vol) / (sum_bid_vol + sum_ask_vol)

   correlations with forward mid-return:
        HP    lag 1  -0.328     lag 10  -0.114
        VFE   lag 1  -0.325     lag 10  -0.117

The negative correlation means: heavy bid book → mid drops next ticks.
Likely interpretation: deep bids are market-maker defenses against falling
prices; the bid load reflects expected downside, not buying interest.

Either way, the signal is large (|corr| > 0.3 at lag 1) and orthogonal to
v29's existing signals (z-score / EMA / counterparty flow).

Structural change (single addition vs v29):
   Add an OBI strength to each product's conviction blend.

   imb = (Σ bid_vol − Σ ask_vol) / (Σ bid_vol + Σ ask_vol)        ∈ [-1, +1]
   obi_dir = -sign(imb)             # contrarian — bid-heavy → bearish
   obi_str = clamp(|imb| − OBI_MIN, 0, OBI_MAX − OBI_MIN) / (OBI_MAX − OBI_MIN)

   Direction filter: contributes only when obi_dir == z_direction (i.e.
   confirmation of MR direction). Otherwise zero.

   conviction = w_z * z_str + w_ema * ema_str + w_inf * inf_str + w_obi * obi_str
   (caps unchanged at min(1.0, ...))

Weights chosen so the OBI component adds modest signal without flooding
the others. v29's weights stay at 0.50/0.30/0.20; new w_obi=0.15.
The total can exceed 1.0 when ALL signals align — clamped at conviction=1.

HP and VFE share the architecture; per-product OBI thresholds.
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


# === HP and VFE configs (v29 + OBI extension) ==============================
HP_CFG = {
    "prefix": "hp", "symbol": "HYDROGEL_PACK", "limit": 200, "fair": 10002,
    "stdev_init": 33.0, "var_alpha": 0.005,
    "qsize": 35, "flat_pull": 1.0, "mr_thresh": 4, "mr_boost": 1.5,
    "z_min": 0.7, "z_max": 2.0,
    "ema_fast": 0.30, "ema_slow": 0.05, "ema_vslow": 0.02, "ema_full": 1.5,
    "informed_lookback": 8, "informed_full": 30,
    "w_z": 0.50, "w_ema": 0.30, "w_inf": 0.20,
    # NEW: OBI signal
    "obi_min": 0.20, "obi_max": 0.80, "w_obi": 0.15,
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
    # NEW: OBI signal
    "obi_min": 0.20, "obi_max": 0.80, "w_obi": 0.15,
    "take_max": 70, "take_offset": 3,
    "base_cap_pct": 0.50, "full_cap_pct": 1.00, "hard_cap_pct": 0.95,
    "mark_weights": {
        "Mark 67": +1.5, "Mark 49": -1.5, "Mark 22": -1.0,
        "Mark 14": -0.5, "Mark 55": +0.5,
    },
}


def conviction_orders(depth, pos, td, market_trades, cfg):
    """Generic conviction-MR engine — v29 + OBI 4th component."""
    if not depth.buy_orders or not depth.sell_orders:
        return []
    bb = max(depth.buy_orders); ba = min(depth.sell_orders)
    mid = (bb + ba) / 2.0
    P = cfg["prefix"]; sym = cfg["symbol"]; LIM = cfg["limit"]; fair = cfg["fair"]

    # Z-score
    dev = mid - fair
    var = td.get(f"_{P}_var", cfg["stdev_init"] ** 2)
    var = (1.0 - cfg["var_alpha"]) * var + cfg["var_alpha"] * (dev * dev)
    td[f"_{P}_var"] = var
    stdev = max(cfg["stdev_init"] * 0.15, var ** 0.5)
    z = dev / stdev
    abs_z = abs(z)
    z_str = 0.0 if abs_z < cfg["z_min"] else min(1.0, (abs_z - cfg["z_min"]) / (cfg["z_max"] - cfg["z_min"]))
    direction = +1 if dev < 0 else -1

    # EMA confluence (same as v29)
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

    # Per-Mark informed flow (same as v29)
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

    # === NEW: Order-Book Imbalance signal (always-on quote-sizing modifier) ===
    # OBI is contrarian (corr -0.33 at lag 1). Heavy bids → expect mid down.
    # Continuous, no threshold. obi_signed in [-1, +1].
    bid_vol_total = sum(depth.buy_orders.values())
    ask_vol_total = sum(-v for v in depth.sell_orders.values())
    obi_signed = 0.0
    if bid_vol_total + ask_vol_total > 0:
        imb = (bid_vol_total - ask_vol_total) / (bid_vol_total + ask_vol_total)
        # Contrarian: -imb gives the predicted direction (bid-heavy → bearish)
        obi_signed = -imb   # already in [-1, +1] since imb is

    # Conviction (z/EMA/informed only — OBI is applied to make-leg, not take)
    if z_str == 0.0:
        conviction = 0.0
    else:
        conviction = cfg["w_z"] * z_str + cfg["w_ema"] * ema_str + cfg["w_inf"] * inf_str

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

    # OBI quote-sizing modifier — always-on, contrarian.
    # obi_signed > 0  → expect mid UP   → bigger buy, smaller sell
    # obi_signed < 0  → expect mid DOWN → smaller buy, bigger sell
    # Coefficient 0.50 means at full obi_signed ±1, sizes shift ±50%.
    obi_coef = 0.50
    bm *= max(0.0, 1.0 + obi_coef * obi_signed)
    sm *= max(0.0, 1.0 - obi_coef * obi_signed)

    bq = max(0, min(int(round(cfg["qsize"] * bm)), LIM - pos - bv))
    sq = max(0, min(int(round(cfg["qsize"] * sm)), LIM + pos - sv))
    if bid_px < ask_px:
        if bq > 0: out.append(Order(sym, int(bid_px), bq))
        if sq > 0: out.append(Order(sym, int(ask_px), -sq))

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
