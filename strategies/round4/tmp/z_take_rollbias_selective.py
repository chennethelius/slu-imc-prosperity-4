"""
Round 4 — z_take_rollbias_selective (TMP): per-asset gated rollbias.

Per-asset PnL audit on round4 d1-d3 (3-day sums vs z_take baseline) showed
the global α=0.0025/k=0.95 rollbias config from z_take_rollbias.py was a
mixed bag:

  HELPS:  VEV_4500 (+4783), VEV_5300 (+2109), VFE (+2057), VEV_4000 (+394)
  HURTS:  HP (-235), VEV_5000 (-472), VEV_5100 (-121), VEV_5200 (-54),
          VEV_5500 (-2316)
  NEUTRAL: VEV_5400 (0)

Surgically disabling rollbias on the losers (k=1, exact static) preserves
the +9,343 gross gain on the winners and reverts the losers to baseline.
Imbalance bias adds nothing on top of rollbias (tested separately —
imb-on-VFE produced +1212 alone but redundant when rollbias is active),
so imb_on is False everywhere.

Each cfg has:
  rollbias_on: True → k = DEFAULT_K_ROLLBIAS (0.95) — enabled assets
               False → k = 1 (= exact static z_take baseline behavior)
  imb_on:      True → adds DEFAULT_K_IMB * imbalance to z (currently unused)

Result on round4 d1-d3, QP=1.0:
                       baseline     global rollbias    selective
  d1                    261,612       260,128            263,925   (+2313)
  d2                    205,530       206,005            205,736   (+206 — new min)
  d3                    266,827       273,981            273,651   (+6824)
  mean                  244,656       246,705            247,771   (+3115)
  min                   205,530       206,005            205,736   (+206)
  mean+min              450,186       452,710            453,507   (+3321)
  3-day sum             733,969       740,114            743,312   (+9343)

Strict Pareto on (mean, min) and improves all 3 days. The aggregate +9343
matches the sum of per-asset gains on the four enabled symbols,
confirming the gating logic is doing exactly what was predicted from the
per-asset audit.
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
    {"symbol": "HYDROGEL_PACK",       "static_mean": 9994, "static_sd": 32.588, "z_thresh": 1.0, "take_size": 17, "limit": 200, "rollbias_on": False, "imb_on": False},
    {"symbol": "VELVETFRUIT_EXTRACT", "static_mean": 5247, "static_sd": 17.091, "z_thresh": 1.0, "take_size": 17, "limit": 200, "rollbias_on": True,  "imb_on": False},
    {"symbol": "VEV_4000",            "static_mean": 1247, "static_sd": 17.114, "z_thresh": 1.0, "take_size": 17, "limit": 300, "rollbias_on": True,  "imb_on": False},
    {"symbol": "VEV_4500",            "static_mean":  747, "static_sd": 17.105, "z_thresh": 1.0, "take_size": 17, "limit": 300, "rollbias_on": True,  "imb_on": False},
    {"symbol": "VEV_5000",            "static_mean":  252, "static_sd": 16.381, "z_thresh": 1.0, "take_size": 17, "limit": 300, "rollbias_on": False, "imb_on": False},
    {"symbol": "VEV_5100",            "static_mean":  163, "static_sd": 15.327, "z_thresh": 1.0, "take_size": 17, "limit": 300, "rollbias_on": False, "imb_on": False},
    {"symbol": "VEV_5200",            "static_mean":   91, "static_sd": 12.796, "z_thresh": 1.0, "take_size": 17, "limit": 300, "rollbias_on": False, "imb_on": False},
    {"symbol": "VEV_5300",            "static_mean":   43, "static_sd":  8.976, "z_thresh": 1.0, "take_size": 17, "limit": 300, "rollbias_on": True,  "imb_on": False},
    {"symbol": "VEV_5400",            "static_mean":   14, "static_sd":  4.608, "z_thresh": 1.0, "take_size": 17, "limit": 300, "rollbias_on": False, "imb_on": False},
    {"symbol": "VEV_5500",            "static_mean":    6, "static_sd":  2.477, "z_thresh": 1.0, "take_size": 17, "limit": 300, "rollbias_on": False, "imb_on": False},
]


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
        for cfg in CFGS:
            ors = _z_take_orders(state, cfg, store)
            if ors:
                orders[cfg["symbol"]] = ors
        return orders, 0, json.dumps(store)
