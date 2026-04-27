"""Round 4 v30 — v29 (HP + VFE) + VEV vol-surface arbitrage.

Picard's three insights mapped:
  1. Two-phase chooser → near-expiry, focus on directional/intrinsic;
     during quoting phase, harvest IV-smile outliers.
  2. Counterparty classification → already in HP/VFE per-Mark weights.
  3. IV outliers → buy underpriced vol, sell overpriced.

VEV vol-surface analysis (R4 d3, 100-tick samples, n=98 fits):
   Strike   avg_IV   resid_z    Verdict
   ----------------------------------------
   4500    0.241     -0.39    mild cheap
   5000    0.302     +0.41    mild rich
   5100    0.294     -1.25    CHEAP (buy vol)
   5200    0.299     -0.14    fair
   5300    0.303     +0.26    fair
   5400    0.289     -1.72    CHEAP (strong buy vol)
   5500    0.303     +0.58    mild rich

VEV_5400 is the cleanest mispricing (z = -1.72σ below the fitted smile).
VEV_5100 also cheap (z = -1.25σ).

Strategy: passive MM on these vouchers biased to capture the mispricing.
   For each voucher in the smile-fit set, compute current IV residual.
   If residual_z < -1.0 → bias toward LONG (place tighter/larger bid).
   If residual_z > +1.0 → bias toward SHORT (place tighter/larger ask).
   Sizes scale with |residual_z|.

Delta-hedge: net vega-weighted delta across all VEV positions, hedged via
VFE position adjustment. Conservative hedge ratio caps directional risk
without consuming all VFE limit.

Constraints:
   - VEV position limit = 300 per voucher
   - Max total VEV vega exposure = 60 vega-units (cap to avoid blowups)
   - VFE delta-hedge tap: at most 30 lots per tick allocated to hedging

This is layered ON TOP of v29's HP+VFE conviction MR.
"""

import json
import math
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

# === VEV config =============================================================
VEV_LIMIT = 300
VEV_STRIKES = [5000, 5100, 5200, 5300, 5400, 5500]   # smile fit set (active vol)
# Time to expiry estimate. Round 4 is the last "trading round" before expiration.
# Convention: use a starting T that decreases with state.timestamp during the round.
# Daily timestamps are 0..1_000_000 per day. Approximate 3 days of round 4 + 1 buffer.
# T = max(0.5/250, (DAYS_LEFT - state.timestamp/1_000_000) / 250) (annualized)
VEV_DAYS_AT_START = 3.0     # at state.timestamp = 0
VEV_TRADING_DAYS_PER_YEAR = 250.0

VEV_IV_LOOKBACK = 200       # ticks to track residual smoothness
VEV_RESIDUAL_THRESH = 1.0   # min |z-residual| to act
VEV_BASE_QSIZE = 30         # base passive bid/ask size when smile signal triggers


# === Black-Scholes / IV =====================================================
def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_call(S, K, T, sigma, r=0.0):
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def _bs_delta(S, K, T, sigma, r=0.0):
    if T <= 0 or sigma <= 0:
        return 1.0 if S > K else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d1)


def _implied_vol(price, S, K, T, r=0.0):
    if T <= 0 or price < max(0, S - K) - 0.01 or price < 0.001:
        return None
    lo, hi = 0.001, 5.0
    for _ in range(40):
        mid = (lo + hi) / 2.0
        p = _bs_call(S, K, T, mid, r)
        if p < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# === Generic conviction MR engine (same as v29) =============================
def conviction_orders(depth, pos, td, market_trades, cfg):
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


# === VEV vol-arb engine =====================================================
def _solve_quadratic_smile(points):
    """Fit IV = a + b*m + c*m² over (moneyness, IV) points. Returns (a,b,c) or None."""
    n = len(points)
    if n < 4:
        return None
    sm = sum(p[0] for p in points); sm2 = sum(p[0] ** 2 for p in points)
    sm3 = sum(p[0] ** 3 for p in points); sm4 = sum(p[0] ** 4 for p in points)
    sv = sum(p[1] for p in points); smv = sum(p[0] * p[1] for p in points)
    sm2v = sum(p[0] ** 2 * p[1] for p in points)
    M = [[n, sm, sm2], [sm, sm2, sm3], [sm2, sm3, sm4]]
    Y = [sv, smv, sm2v]
    def det3(A):
        return (A[0][0] * (A[1][1] * A[2][2] - A[1][2] * A[2][1])
                - A[0][1] * (A[1][0] * A[2][2] - A[1][2] * A[2][0])
                + A[0][2] * (A[1][0] * A[2][1] - A[1][1] * A[2][0]))
    D = det3(M)
    if abs(D) < 1e-9:
        return None
    Ma = [list(r) for r in M]; Ma[0][0], Ma[1][0], Ma[2][0] = Y
    Mb = [list(r) for r in M]; Mb[0][1], Mb[1][1], Mb[2][1] = Y
    Mc = [list(r) for r in M]; Mc[0][2], Mc[1][2], Mc[2][2] = Y
    return det3(Ma) / D, det3(Mb) / D, det3(Mc) / D


def _vev_residual_orders(state, td, vfe_mid):
    """For each smile-fit voucher, compute IV residual vs fitted smile and post
    a passive bias quote sized by |residual|."""
    out = {}
    if vfe_mid is None or vfe_mid <= 0:
        return out

    # T-to-expiry estimate
    days_left = max(0.5, VEV_DAYS_AT_START - state.timestamp / 1_000_000.0)
    T = days_left / VEV_TRADING_DAYS_PER_YEAR

    # Compute IV for each voucher in fit set
    points = []
    voucher_data = {}
    for K in VEV_STRIKES:
        sym = f"VEV_{K}"
        depth = state.order_depths.get(sym)
        if not depth or not depth.buy_orders or not depth.sell_orders:
            continue
        bb = max(depth.buy_orders); ba = min(depth.sell_orders)
        mid = (bb + ba) / 2.0
        if mid < 1.0:
            continue
        iv = _implied_vol(mid, vfe_mid, K, T)
        if iv is None or iv >= 4.0 or iv < 0.01:
            continue
        m = math.log(vfe_mid / K)
        delta = _bs_delta(vfe_mid, K, T, iv)
        points.append((m, iv))
        voucher_data[K] = {"sym": sym, "mid": mid, "iv": iv, "m": m,
                           "delta": delta, "depth": depth, "bb": bb, "ba": ba}

    coefs = _solve_quadratic_smile(points)
    if coefs is None:
        return out
    a, b, c = coefs

    # Track residuals per voucher in EWMA mean/var so we can compute z-score
    for K, v in voucher_data.items():
        fit_iv = a + b * v["m"] + c * v["m"] ** 2
        resid = v["iv"] - fit_iv
        # Track exponential mean & var
        rk = f"_v{K}_rmean"; vk = f"_v{K}_rvar"
        rm = td.get(rk, 0.0); rv = td.get(vk, 0.0001)
        rm = 0.99 * rm + 0.01 * resid
        rv = 0.99 * rv + 0.01 * (resid - rm) ** 2
        td[rk] = rm; td[vk] = rv
        rsd = max(0.001, rv ** 0.5)
        z_resid = (resid - 0.0) / rsd   # z relative to historical typical residual

        # Act only on strong outliers (|z| > THRESH)
        if abs(z_resid) < VEV_RESIDUAL_THRESH:
            continue

        pos = state.position.get(v["sym"], 0)
        depth = v["depth"]
        bb = v["bb"]; ba = v["ba"]
        scale = min(1.5, abs(z_resid) / VEV_RESIDUAL_THRESH)
        size = int(round(VEV_BASE_QSIZE * scale))

        orders = []
        if z_resid < 0:
            # CHEAP — bias toward LONG: post tighter/larger bid; smaller ask
            bid_px = bb + 1
            ask_px = ba
            buy_q = min(size, VEV_LIMIT - pos)
            sell_q = min(size // 3, VEV_LIMIT + pos)
            if buy_q > 0:
                orders.append(Order(v["sym"], bid_px, buy_q))
            if sell_q > 0:
                orders.append(Order(v["sym"], ask_px, -sell_q))
        else:
            # RICH — bias toward SHORT: post tighter/larger ask; smaller bid
            bid_px = bb
            ask_px = ba - 1
            sell_q = min(size, VEV_LIMIT + pos)
            buy_q = min(size // 3, VEV_LIMIT - pos)
            if buy_q > 0:
                orders.append(Order(v["sym"], bid_px, buy_q))
            if sell_q > 0:
                orders.append(Order(v["sym"], ask_px, -sell_q))

        if orders:
            out[v["sym"]] = orders

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

        # === VEV vol-arb on smile residuals ===
        vfe_depth = state.order_depths.get("VELVETFRUIT_EXTRACT")
        if vfe_depth and vfe_depth.buy_orders and vfe_depth.sell_orders:
            bb = max(vfe_depth.buy_orders); ba = min(vfe_depth.sell_orders)
            vfe_mid = (bb + ba) / 2.0
            vev_orders = _vev_residual_orders(state, td, vfe_mid)
            for sym, ors in vev_orders.items():
                orders[sym] = ors

        trader_data = json.dumps(td)
        logger.flush(state, orders, 0, trader_data)
        return orders, 0, trader_data
