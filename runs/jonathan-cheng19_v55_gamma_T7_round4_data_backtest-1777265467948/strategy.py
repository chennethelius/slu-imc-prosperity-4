"""Round 4 v54 — STANDALONE long-gamma delta-hedging test (no v52 overlay).

User directive: experiment with long-gamma delta-hedging.

Pure-gamma strategy:
  - Hold target +N calls on an ATM VEV strike (long gamma)
  - Continuously delta-hedge by shorting VFE: short_qty = round(opt_pos × Δ)
  - Profit = realized vol > implied vol × gamma realized
  - Cost = theta decay each tick

We test if this is net positive on R4 data ALONE (no MR alpha overlay).
If standalone gamma makes money, build v55 = v52 + gamma layer.
If it loses, the IV >> realized-vol thesis is confirmed and gamma is dead.

Hedge ratio computed via Black-Scholes.
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


# === Gamma config ===========================================================
GAMMA_VOUCHER = "VEV_5300"   # ATM-ish (S~5249, K=5300, slightly OTM)
GAMMA_STRIKE = 5300
GAMMA_TARGET_POS = 100       # bigger position than v54 to scale realized gamma
GAMMA_BUILD_RATE = 10        # max lots/tick to build position
GAMMA_REBAL_THRESH = 3       # tighter rebalance to capture more vol cycles

# Time to expiry: vouchers expire end of round 5 (~7 days at R4-d1 start)
# User's analysis: at T=7d, IV ~19% across strikes vs VFE realized vol ~47%
# → long-gamma should profit from the IV<<RV gap
DAYS_AT_START = 7.0
TRADING_DAYS = 250.0
GAMMA_TARGET_POS = 100   # bigger position than v54's 30 to scale gamma


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_call(S, K, T, sigma, r=0.0):
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K)
    d1 = (math.log(S/K) + (r + 0.5*sigma*sigma)*T) / (sigma*math.sqrt(T))
    d2 = d1 - sigma*math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r*T) * _norm_cdf(d2)


def _bs_delta(S, K, T, sigma, r=0.0):
    if T <= 0 or sigma <= 0:
        return 1.0 if S > K else 0.0
    d1 = (math.log(S/K) + (r + 0.5*sigma*sigma)*T) / (sigma*math.sqrt(T))
    return _norm_cdf(d1)


def _implied_vol(price, S, K, T):
    if T <= 0 or price < max(0, S-K) - 0.01 or price < 0.001:
        return None
    lo, hi = 0.001, 5.0
    for _ in range(40):
        m = (lo + hi) / 2
        if _bs_call(S, K, T, m) < price:
            lo = m
        else:
            hi = m
    return (lo + hi) / 2


class Trader:
    def bid(self):
        return 0

    def run(self, state: TradingState):
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}

        orders: dict[str, list[Order]] = {}

        vfe_depth = state.order_depths.get("VELVETFRUIT_EXTRACT")
        opt_depth = state.order_depths.get(GAMMA_VOUCHER)
        if not vfe_depth or not opt_depth:
            trader_data = json.dumps(td)
            logger.flush(state, orders, 0, trader_data)
            return orders, 0, trader_data
        if not vfe_depth.buy_orders or not vfe_depth.sell_orders:
            trader_data = json.dumps(td)
            logger.flush(state, orders, 0, trader_data)
            return orders, 0, trader_data
        if not opt_depth.buy_orders or not opt_depth.sell_orders:
            trader_data = json.dumps(td)
            logger.flush(state, orders, 0, trader_data)
            return orders, 0, trader_data

        vfe_bb = max(vfe_depth.buy_orders); vfe_ba = min(vfe_depth.sell_orders)
        vfe_mid = (vfe_bb + vfe_ba) / 2.0
        opt_bb = max(opt_depth.buy_orders); opt_ba = min(opt_depth.sell_orders)
        opt_mid = (opt_bb + opt_ba) / 2.0

        # Time to expiry (decreases with state.timestamp)
        days_left = max(0.5, DAYS_AT_START - state.timestamp / 1_000_000.0)
        T = days_left / TRADING_DAYS
        iv = _implied_vol(opt_mid, vfe_mid, GAMMA_STRIKE, T)
        if iv is None:
            iv = 0.30
        delta = _bs_delta(vfe_mid, GAMMA_STRIKE, T, iv)

        # === Build option position toward target ===
        opt_pos = state.position.get(GAMMA_VOUCHER, 0)
        opt_orders = []
        if opt_pos < GAMMA_TARGET_POS:
            # Buy at best ask
            avail = -opt_depth.sell_orders[opt_ba]
            qty = min(GAMMA_TARGET_POS - opt_pos, GAMMA_BUILD_RATE, avail, 300 - opt_pos)
            if qty > 0:
                opt_orders.append(Order(GAMMA_VOUCHER, int(opt_ba), qty))
                # Update local for hedge calc
                opt_pos += qty

        # === Delta hedge in VFE ===
        # For long calls: delta > 0, so hedge = SHORT VFE
        target_vfe_hedge = -int(round(opt_pos * delta))
        vfe_pos = state.position.get("VELVETFRUIT_EXTRACT", 0)
        vfe_diff = target_vfe_hedge - vfe_pos
        vfe_orders = []
        if abs(vfe_diff) > GAMMA_REBAL_THRESH:
            if vfe_diff > 0:
                # Need more long VFE (or less short)
                avail = -vfe_depth.sell_orders[vfe_ba]
                qty = min(vfe_diff, 10, avail, 200 - vfe_pos)
                if qty > 0:
                    vfe_orders.append(Order("VELVETFRUIT_EXTRACT", int(vfe_ba), qty))
            else:
                # Need more short VFE
                avail = vfe_depth.buy_orders[vfe_bb]
                qty = min(-vfe_diff, 10, avail, 200 + vfe_pos)
                if qty > 0:
                    vfe_orders.append(Order("VELVETFRUIT_EXTRACT", int(vfe_bb), -qty))

        if opt_orders:
            orders[GAMMA_VOUCHER] = opt_orders
        if vfe_orders:
            orders["VELVETFRUIT_EXTRACT"] = vfe_orders

        trader_data = json.dumps(td)
        logger.flush(state, orders, 0, trader_data)
        return orders, 0, trader_data
