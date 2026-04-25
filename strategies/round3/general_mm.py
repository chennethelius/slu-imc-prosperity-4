"""
Multi-product market maker — round 3.

Pipeline per product per tick (see make_orders):
    fair  = full_depth_mid(book)
    fair += aggressor_lambda × rolling aggressor flow
    c     = SPREAD_FRACTION, widened ∝ realized_vol / baseline_vol
    out   = take(book mispriced past fair) + bid/ask quote at fair ± c·gap

Per-product config is plain data (PRODUCTS). To add a product, append a dict;
to tune one, edit its lambda. Only signals with measurable PnL impact survive.
"""

import json
import math

from datamodel import Order, TradingState

SPREAD_FRACTION = 0.5
SKEW_PER_UNIT = 0.02
VOL_WINDOW = 100
VOL_SCALE_MAX = 2.0
TAKE_WIDTH = 1
AGGRESSOR_WINDOW = 10


# ---------- order book ------------------------------------------------------


def search_sells(depth):
    for p in sorted(depth.sell_orders):
        yield p, -depth.sell_orders[p]


def search_buys(depth):
    for p in sorted(depth.buy_orders, reverse=True):
        yield p, depth.buy_orders[p]


def full_depth_mid(depth):
    """Per-side VWAP, then midpoint of the two side-VWAPs."""
    bids, asks = list(search_buys(depth)), list(search_sells(depth))
    bv, av = sum(v for _, v in bids), sum(v for _, v in asks)
    if bv <= 0 or av <= 0:
        return (max(depth.buy_orders) + min(depth.sell_orders)) / 2
    bid_vwap = sum(p * v for p, v in bids) / bv
    ask_vwap = sum(p * v for p, v in asks) / av
    return (bid_vwap + ask_vwap) / 2


def realized_vol(mids):
    """Sample std of tick-to-tick mid changes."""
    if len(mids) < 2:
        return 0.0
    diffs = [mids[i] - mids[i - 1] for i in range(1, len(mids))]
    mean = sum(diffs) / len(diffs)
    return math.sqrt(sum((d - mean) ** 2 for d in diffs) / len(diffs))


def aggressor_side(price, best_bid, best_ask):
    """+1 buyer aggressor, -1 seller aggressor, 0 ambiguous."""
    return 1 if price >= best_ask else -1 if price <= best_bid else 0


def trim(history, window):
    if len(history) > window:
        del history[: len(history) - window]


# ---------- pipeline -------------------------------------------------------


def adjust_fair_for_aggressor_flow(cfg, fair, best_bid, best_ask, state, scratch):
    lam = cfg.get("aggressor_lambda", 0.0)
    if lam == 0.0:
        return fair
    flow = sum(
        aggressor_side(t.price, best_bid, best_ask) * t.quantity
        for t in state.market_trades.get(cfg["product"], [])
    )
    history = scratch.setdefault("agg_flow", [])
    history.append(flow)
    trim(history, AGGRESSOR_WINDOW)
    return fair + lam * sum(history)


def vol_widened_spread(cfg, scratch, best_bid, best_ask):
    mids = scratch.setdefault("mids", [])
    mids.append((best_bid + best_ask) / 2)
    trim(mids, VOL_WINDOW)
    if len(mids) < VOL_WINDOW // 2:
        return SPREAD_FRACTION
    baseline = cfg.get("baseline_vol", 1.5)
    vol = realized_vol(mids)
    if vol <= baseline or baseline <= 0:
        return SPREAD_FRACTION
    return min(1.0, SPREAD_FRACTION * min(VOL_SCALE_MAX, vol / baseline))


def take_orders(cfg, depth, fair, position):
    """Cross the book where it's mispriced past fair±TAKE_WIDTH."""
    product, limit = cfg["product"], cfg["position_limit"]
    out, bought, sold = [], 0, 0
    for price, qty in search_sells(depth):
        if price >= fair - TAKE_WIDTH:
            break
        cap = limit - position - bought
        if cap <= 0:
            break
        take = min(qty, cap)
        out.append(Order(product, price, take))
        bought += take
    for price, qty in search_buys(depth):
        if price <= fair + TAKE_WIDTH:
            break
        cap = limit + position - sold
        if cap <= 0:
            break
        take = min(qty, cap)
        out.append(Order(product, price, -take))
        sold += take
    return out, bought, sold


def make_quote(cfg, fair, best_bid, best_ask, position, c, bought, sold):
    """One bid + one ask, c-fraction inside the book, inventory-skewed."""
    product, limit = cfg["product"], cfg["position_limit"]
    skew = position * SKEW_PER_UNIT
    bid_px = min(math.floor(fair - c * (fair - best_bid) - skew), best_ask - 1)
    ask_px = max(math.ceil(fair + c * (best_ask - fair) - skew), best_bid + 1)
    qsize = cfg.get("quote_size", 20)
    buy = max(0, min(qsize, limit - position - bought))
    sell = max(0, min(qsize, limit + position - sold))
    out = []
    if buy > 0 and bid_px < ask_px:
        out.append(Order(product, bid_px, buy))
    if sell > 0 and ask_px > bid_px:
        out.append(Order(product, ask_px, -sell))
    return out


def make_orders(cfg, state, scratch):
    """One product, one tick — full pipeline."""
    depth = state.order_depths.get(cfg["product"])
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    best_bid, best_ask = max(depth.buy_orders), min(depth.sell_orders)
    fair = full_depth_mid(depth)
    fair = adjust_fair_for_aggressor_flow(cfg, fair, best_bid, best_ask, state, scratch)
    c = vol_widened_spread(cfg, scratch, best_bid, best_ask)
    position = state.position.get(cfg["product"], 0)
    takes, bought, sold = take_orders(cfg, depth, fair, position)
    quotes = make_quote(cfg, fair, best_bid, best_ask, position, c, bought, sold)
    return takes + quotes


# ---------- per-product configuration --------------------------------------
# aggressor_lambda is anchored to offline-measured 20-tick informedness:
#   HYDROGEL  -0.47  → fade  (lambda < 0, NOISE flow)
#   VELVET    +0.59  → lean  (lambda > 0, INFORMED flow)
#   VEV_4000  +0.76  → lean  (long-horizon informed)
PRODUCTS = [
    {"product": "HYDROGEL_PACK",       "position_limit": 200, "aggressor_lambda": -0.010},
    {"product": "VELVETFRUIT_EXTRACT", "position_limit": 200, "aggressor_lambda":  0.012},
    {"product": "VEV_4000", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5,
     "aggressor_lambda": 0.015},
    {"product": "VEV_4500", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5},
    {"product": "VEV_5000", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5},
    {"product": "VEV_5100", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5},
    {"product": "VEV_5200", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5},
    {"product": "VEV_5300", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5},
    {"product": "VEV_5400", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5},
    {"product": "VEV_5500", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.3},
    # VEV_6000 / VEV_6500: pinned at 0.5, no edge.
]


class Trader:
    def bid(self):
        return 15

    def run(self, state: TradingState):
        try:
            store = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            store = {}
        orders = {}
        for cfg in PRODUCTS:
            ors = make_orders(cfg, state, store.setdefault(cfg["product"], {}))
            if ors:
                orders[cfg["product"]] = ors
        return orders, 0, json.dumps(store)
