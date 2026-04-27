"""Round 4 v69 — v62 + VEV→VFE lead-lag MM-skew (BREAKTHROUGH attempt).

Cross-product price-discovery signal (validated empirically R4-d3, n=9999):
   vev_5000_delta = 0.673 * vfe_delta - 0.002   (R² = 0.58)

When residual = vev_delta - 0.673*vfe_delta:
   residual > +0.5: VEV overshot → fwd VFE 5-tick mean +0.265
   residual < -0.5: VEV undershot → fwd VFE 5-tick mean -0.328

v68 tried this in conviction blend (20% weight) — only -390 PnL,
diluted by the blend. v69 applies it DIRECTLY to MM-leg sizing on VFE:
   residual > +0.5 (VFE bullish): bigger bid, smaller ask
   residual < -0.5 (VFE bearish): bigger ask, smaller bid

The MM-leg quotes get filled from passive flow — when our prediction
agrees with the imminent move, our oversized side captures more fills
at favorable prices.

Original v52 docstring follows:

Round 4 v52 — v42 + dynamic VEV soft_cap based on conviction.

Microstructure analysis showed VEV_4000/4500 max position only reached
87/169 — far below their soft_cap of 250. Yet at very high conviction
events, the alpha is reliable enough to commit FULL inventory.

v52 makes VEV soft_caps DYNAMIC per voucher, scaling from soft_cap (base)
to soft_cap_max at conviction = 1.0:

   dyn_soft_cap = soft_cap + (soft_cap_max - soft_cap) * conviction

At conviction 0.5: VEV_4000 cap = 275 (mid).
At conviction 1.0: VEV_4000 cap = 300 (full limit, was 250 in v42).

This unlocks the FULL chain capacity only when alpha is highest-conviction,
preserving v42's safety margin at lower conviction.

Original v42 docstring follows:

Round 4 v42 — v41 + VEV_5200 added to delta-extension chain.

v41 added MM-leg conviction boost (+4.5k PnL). v42 stacks one more
strike (VEV_5200, delta ~0.65, premium ~$30) for another 300 lots of
capacity in the chain.

Original v41 docstring follows:

Round 4 v41 — v35 with conviction-boosted MM-leg sizes.

User directive: "find a way to scale up conviction or something to utilize
the excess inventory space."

v40 (quadratic take scaling) failed — bigger takes per signal didn't pay
off proportionally. Signal frequency is the bottleneck for the take leg,
not size per take.

v41 tries a different angle: scale the always-on MM-leg sizes with
conviction. When conviction is high (signal aligned), MM quotes get
larger on the SIGNAL-CONFIRMING side. This captures more passive flow
during alpha events without the burst-MTM risk of bigger active takes.

   bm *= (1 + MM_BOOST × conviction)   when direction > 0 (bullish)
   sm *= (1 + MM_BOOST × conviction)   when direction < 0 (bearish)

MM_BOOST = 1.0: at conviction=1, MM quote doubles on the alpha side.

Rationale: passive fills at our touch +1 price are already profitable
(we're inside spread). Bigger size when conviction is high means we
capture more of the (~free) spread. No additional MTM burst — the
position grows gradually as fills land, not instantly like a take.

Original v35 docstring follows:

Round 4 v35 — v34 + VEV_5100 in delta-extension chain.

VEV_5100 has delta ≈ 0.85, premium ≈ $16. Less efficient delta capture
than 4000/4500/5000, more sensitive to premium dynamics, but adds another
300 lots of capacity. Test whether the marginal voucher pays for its
premium-decay risk.

Original v34 docstring follows:

Round 4 v34 — v33 + VEV_5000 in delta-extension chain.

v33 used VEV_4000 (delta=1.0) + VEV_4500 (delta=1.0). v34 adds VEV_5000
(delta ≈ 0.96, ITM by $50 at S~5249) for another 300 lots of capacity.
VEV_5000 trades at intrinsic + ~5 ticks (small time premium, roughly
flat over short holds — cancels in entry/exit).

Capacity:  v33: 800 delta units total → v34: 1100 delta units.

Original v33 docstring follows:

Structural addition: when VFE conviction-take fires and would benefit from
larger position, route the OVERFLOW into VEV_4000 / VEV_4500 (deep ITM
calls trading at intrinsic, delta = 1.0). Each adds 300 lots of effective
VFE-delta capacity.

Capacity math:
   VFE alone:         200 delta units (limit)
   + VEV_4000 ext:    300 more delta units
   + VEV_4500 ext:    300 more delta units
   Total potential: 800 delta units (4x amplification of VFE alpha)

Key observations from R3+R4 data:
   VEV_4000 trades at intrinsic = VFE - 4000  (90.6% of ticks at exactly +0)
   VEV_4500 trades at intrinsic = VFE - 4500  (91.2% of ticks at exactly +0)
   Counterparty: Mark 14 / Mark 38 are two-way MMs on these (no edge)

Implementation:
   When VFE conviction take is non-zero:
     1. Compute total target delta = TAKE_MAX × conviction
     2. Walk VFE book first (cheaper bid-ask cost; native product)
     3. If still overflow remaining, walk VEV_4000 at intrinsic ± offset
     4. Then VEV_4500 if still overflow
   For exits:
     - VEV_4000/4500 carry their own MM leg quoted at intrinsic ± 1
     - When VFE conviction direction reverses, walk VEV books to flatten

The ITM vouchers track VFE 1:1 in delta. No separate alpha; pure capacity.
The bid-ask cost is roughly the same (1-2 ticks per round trip). Net edge
comes from running the SAME alpha on more capital.

HP module unchanged.
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

# === ITM VEV delta-extension config =========================================
# When VFE conviction take wants more capacity than VFE alone provides,
# route overflow into these. Each provides 300 lots of delta-1 exposure.
VEV_DELTA_VOUCHERS = [
    # v62: VEV_5300 added (delta ~0.30, premium ~$80, most OTM in chain)
    {"symbol": "VEV_4000", "strike": 4000, "limit": 300, "soft_cap": 250, "soft_cap_max": 300, "offset": 2},
    {"symbol": "VEV_4500", "strike": 4500, "limit": 300, "soft_cap": 250, "soft_cap_max": 300, "offset": 2},
    {"symbol": "VEV_5000", "strike": 5000, "limit": 300, "soft_cap": 200, "soft_cap_max": 250, "offset": 8},
    {"symbol": "VEV_5100", "strike": 5100, "limit": 300, "soft_cap": 150, "soft_cap_max": 180, "offset": 18},
    {"symbol": "VEV_5200", "strike": 5200, "limit": 300, "soft_cap": 100, "soft_cap_max": 130, "offset": 38},
    {"symbol": "VEV_5300", "strike": 5300, "limit": 300, "soft_cap": 80, "soft_cap_max": 100, "offset": 90},
]
VEV_INTRINSIC_OFFSET = 2     # default offset, overridden by per-voucher


def _conviction_signals(depth, pos, td, market_trades, cfg):
    """Compute z direction, conviction, and book references — same as v29."""
    if not depth.buy_orders or not depth.sell_orders:
        return None
    bb = max(depth.buy_orders); ba = min(depth.sell_orders)
    mid = (bb + ba) / 2.0
    P = cfg["prefix"]; LIM = cfg["limit"]; fair = cfg["fair"]

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
    return {"bb": bb, "ba": ba, "mid": mid, "direction": direction, "conviction": conviction}


def _conviction_orders_with_extension(depth, pos, td, market_trades, cfg, state, all_orders):
    """v29 conviction-MR with VFE-delta extension to ITM VEV vouchers.
    Only applies the extension to VFE (not HP). The extension is invoked
    after VFE's primary take fills its own room.
    """
    sig = _conviction_signals(depth, pos, td, market_trades, cfg)
    if sig is None:
        return []
    bb = sig["bb"]; ba = sig["ba"]; mid = sig["mid"]
    direction = sig["direction"]; conviction = sig["conviction"]
    sym = cfg["symbol"]; LIM = cfg["limit"]; fair = cfg["fair"]

    out = []; bv = sv = 0
    cap_lots = cfg["hard_cap_pct"] * LIM

    # Hard-cap unwind
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

    # Conviction take in primary product
    primary_target = int(round(cfg["take_max"] * conviction)) if conviction > 0 else 0
    primary_taken = 0
    if conviction > 0:
        if direction > 0 and pos_after < soft_cap:
            max_pay = fair + cfg["take_offset"]; rem = primary_target
            for ask in sorted(depth.sell_orders):
                if ask > max_pay or rem <= 0: break
                avail = -depth.sell_orders[ask]
                room = LIM - pos - bv; cap_room = int(soft_cap - pos_after)
                qty = min(avail, room, cap_room, rem)
                if qty <= 0: break
                out.append(Order(sym, ask, qty)); bv += qty; rem -= qty
                primary_taken += qty
                pos_after = pos + bv - sv
        elif direction < 0 and pos_after > -soft_cap:
            min_recv = fair - cfg["take_offset"]; rem = primary_target
            for bid in sorted(depth.buy_orders, reverse=True):
                if bid < min_recv or rem <= 0: break
                avail = depth.buy_orders[bid]
                room = LIM + pos - sv; cap_room = int(soft_cap + pos_after)
                qty = min(avail, room, cap_room, rem)
                if qty <= 0: break
                out.append(Order(sym, bid, -qty)); sv += qty; rem -= qty
                primary_taken += qty
                pos_after = pos + bv - sv

    # === VFE-delta EXTENSION: route overflow into VEV_4000 / VEV_4500 ===
    # Only for VFE (HP doesn't have ITM voucher proxies with right strike)
    if cfg["prefix"] == "vfe" and conviction > 0 and primary_target > primary_taken:
        overflow = primary_target - primary_taken
        vfe_mid_int = int(round(mid))   # for intrinsic computation
        for v in VEV_DELTA_VOUCHERS:
            if overflow <= 0: break
            v_sym = v["symbol"]
            v_strike = v["strike"]
            v_limit = v["limit"]
            # v52: dynamic soft_cap scales with conviction
            v_soft_cap_base = v["soft_cap"]
            v_soft_cap_max = v.get("soft_cap_max", v_soft_cap_base)
            v_soft_cap = int(round(v_soft_cap_base + (v_soft_cap_max - v_soft_cap_base) * conviction))
            v_depth = state.order_depths.get(v_sym)
            if not v_depth or not v_depth.buy_orders or not v_depth.sell_orders:
                continue
            v_pos = state.position.get(v_sym, 0)
            intrinsic = vfe_mid_int - v_strike

            v_orders = []
            v_bv = v_sv = 0
            voucher_offset = v.get("offset", VEV_INTRINSIC_OFFSET)
            if direction > 0 and v_pos < v_soft_cap:
                max_pay = intrinsic + voucher_offset
                rem = overflow
                for ask in sorted(v_depth.sell_orders):
                    if ask > max_pay or rem <= 0: break
                    avail = -v_depth.sell_orders[ask]
                    room = v_limit - v_pos - v_bv
                    cap_room = max(0, v_soft_cap - v_pos)
                    qty = min(avail, room, cap_room, rem)
                    if qty <= 0: break
                    v_orders.append(Order(v_sym, ask, qty)); v_bv += qty
                    rem -= qty
                    overflow -= qty
            elif direction < 0 and v_pos > -v_soft_cap:
                min_recv = intrinsic - voucher_offset
                rem = overflow
                for bid in sorted(v_depth.buy_orders, reverse=True):
                    if bid < min_recv or rem <= 0: break
                    avail = v_depth.buy_orders[bid]
                    room = v_limit + v_pos - v_sv
                    cap_room = max(0, v_soft_cap + v_pos)
                    qty = min(avail, room, cap_room, rem)
                    if qty <= 0: break
                    v_orders.append(Order(v_sym, bid, -qty)); v_sv += qty
                    rem -= qty
                    overflow -= qty
            if v_orders:
                all_orders[v_sym] = v_orders

    # MM leg with v41 conviction-boost on alpha-aligned side
    pos_after = pos + bv - sv
    mr_dir = +1 if mid < fair - cfg["mr_thresh"] else (-1 if mid > fair + cfg["mr_thresh"] else 0)
    bid_px = min(bb + 1, fair - 1); ask_px = max(ba - 1, fair + 1)
    ratio = pos_after / LIM
    bm = max(0.0, 1.0 - cfg["flat_pull"] * ratio); sm = max(0.0, 1.0 + cfg["flat_pull"] * ratio)
    if mr_dir > 0: bm *= cfg["mr_boost"]
    elif mr_dir < 0: sm *= cfg["mr_boost"]
    # v41: conviction boost on alpha-aligned MM quote
    MM_BOOST = 1.0
    if conviction > 0:
        if direction > 0:
            bm *= (1.0 + MM_BOOST * conviction)
        elif direction < 0:
            sm *= (1.0 + MM_BOOST * conviction)

    # v69: VEV→VFE lead-lag MM-skew (VFE only)
    if cfg["prefix"] == "vfe":
        prev_vfe = td.get("_vfe_prev_mid", mid)
        prev_vev = td.get("_vev_prev_mid", None)
        vev_depth = state.order_depths.get("VEV_5000")
        if vev_depth and vev_depth.buy_orders and vev_depth.sell_orders:
            vev_mid = (max(vev_depth.buy_orders) + min(vev_depth.sell_orders)) / 2.0
            if prev_vev is not None:
                vfe_delta_t = mid - prev_vfe
                vev_delta_t = vev_mid - prev_vev
                residual = vev_delta_t - 0.673 * vfe_delta_t
                # residual > 0 → VFE bullish forecast → bigger bid, smaller ask
                LAG_SCALE = 0.4
                if residual > 0.5:
                    factor = min(2.0, 1.0 + LAG_SCALE * (residual - 0.5))
                    bm *= factor
                    sm *= max(0.4, 1.0 - LAG_SCALE * (residual - 0.5) * 0.5)
                elif residual < -0.5:
                    factor = min(2.0, 1.0 + LAG_SCALE * (-residual - 0.5))
                    sm *= factor
                    bm *= max(0.4, 1.0 - LAG_SCALE * (-residual - 0.5) * 0.5)
            td["_vev_prev_mid"] = vev_mid
        td["_vfe_prev_mid"] = mid

    bq = max(0, min(int(round(cfg["qsize"] * bm)), LIM - pos - bv))
    sq = max(0, min(int(round(cfg["qsize"] * sm)), LIM + pos - sv))
    if bid_px < ask_px:
        if bq > 0: out.append(Order(sym, int(bid_px), bq))
        if sq > 0: out.append(Order(sym, int(ask_px), -sq))

    return out


def _vev_flatten_orders(state, td):
    """Passive close-out for any non-zero VEV_4000/4500 inventory.
    Posts ask 1 above intrinsic if long, bid 1 below intrinsic if short.
    """
    out_by_sym = {}
    vfe_depth = state.order_depths.get("VELVETFRUIT_EXTRACT")
    if not vfe_depth or not vfe_depth.buy_orders or not vfe_depth.sell_orders:
        return out_by_sym
    vfe_mid = (max(vfe_depth.buy_orders) + min(vfe_depth.sell_orders)) / 2.0
    vfe_mid_int = int(round(vfe_mid))
    for v in VEV_DELTA_VOUCHERS:
        v_sym = v["symbol"]; v_strike = v["strike"]; v_limit = v["limit"]
        v_pos = state.position.get(v_sym, 0)
        if v_pos == 0: continue
        v_depth = state.order_depths.get(v_sym)
        if not v_depth or not v_depth.buy_orders or not v_depth.sell_orders:
            continue
        intrinsic = vfe_mid_int - v_strike
        # For ATM vouchers (5000+), the touch price is at intrinsic + premium.
        # Use observed best bid/ask as the closing reference instead of intrinsic.
        v_bb = max(v_depth.buy_orders); v_ba = min(v_depth.sell_orders)
        orders = []
        if v_pos > 0:
            # Sell at best ask (or 1 inside) — uses live touch, not stale intrinsic
            ask_px = max(intrinsic + 1, v_ba - 1)
            sell_q = min(v_pos, 50, v_limit + v_pos)
            if sell_q > 0:
                orders.append(Order(v_sym, ask_px, -sell_q))
        elif v_pos < 0:
            bid_px = min(intrinsic - 1, v_bb + 1)
            buy_q = min(-v_pos, 50, v_limit - v_pos)
            if buy_q > 0:
                orders.append(Order(v_sym, bid_px, buy_q))
        if orders:
            out_by_sym[v_sym] = orders
    return out_by_sym


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
                ors = _conviction_orders_with_extension(depth, pos, td, trades, cfg, state, orders)
                if ors:
                    orders[sym] = ors

        # Passive flatten of any inherited VEV inventory
        flatten = _vev_flatten_orders(state, td)
        for s, ors in flatten.items():
            if s not in orders:   # don't override active extension orders
                orders[s] = ors
            else:
                orders[s].extend(ors)

        trader_data = json.dumps(td)
        logger.flush(state, orders, 0, trader_data)
        return orders, 0, trader_data
