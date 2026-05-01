"""
Anchor-divergence multi-product market maker — AGGRESSIVE variant.

`diverge_threshold` is hand-tuned BELOW the 95th-percentile (HYDROGEL=40 vs
the strict 55, etc.). Triggers more often → more trades → more PnL on the
historical tape. This carries some overfit risk: thresholds were chosen with
visibility into the full 3-day dataset.

Backtest result on round 3:
  Total PnL = +502,822   (vs validated variant +314,262 in-sample)
  Position utilization 97-98% across products.

Live results will likely fall between the validated (+314k) and aggressive
(+503k) numbers, since live IMC has finite competing flow that won't match
the backtester's tape-share fills at large quote sizes.

Same code shape as zscore_validated.py — only diverge_threshold values
differ. Pipeline:
  1. snapshot  — book + full_depth_mid fair
  2. signal    — fair += aggressor_lambda × rolling aggressor flow
  3. anchor    — expanding-window mean of all observed mids
  4. diverge   — |mid − anchor| ≥ threshold → contra-direction take
  5. spread    — vol-aware widening of SPREAD_FRACTION
  6. orders    — take phase + bid/ask quote, inventory-skewed
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
ANCHOR_WARMUP = 100   # was 1000; IMC only tests 1000 ticks per session, so
                       # we need the anchor active well before that to capture
                       # divergence trades during the live window
DIVERGE_TAKE_SIZE = 30


# ---------- order book helpers ---------------------------------------------


def search_sells(depth):
    for p in sorted(depth.sell_orders):
        yield p, -depth.sell_orders[p]


def search_buys(depth):
    for p in sorted(depth.buy_orders, reverse=True):
        yield p, depth.buy_orders[p]


def full_depth_mid(depth):
    bids, asks = list(search_buys(depth)), list(search_sells(depth))
    bv, av = sum(v for _, v in bids), sum(v for _, v in asks)
    if bv <= 0 or av <= 0:
        return (max(depth.buy_orders) + min(depth.sell_orders)) / 2
    return (sum(p * v for p, v in bids) / bv + sum(p * v for p, v in asks) / av) / 2


def realized_vol(mids):
    if len(mids) < 2:
        return 0.0
    diffs = [mids[i] - mids[i - 1] for i in range(1, len(mids))]
    mean = sum(diffs) / len(diffs)
    return math.sqrt(sum((d - mean) ** 2 for d in diffs) / len(diffs))


def aggressor_side(price, best_bid, best_ask):
    return 1 if price >= best_ask else -1 if price <= best_bid else 0


def trim(history, window):
    if len(history) > window:
        del history[: len(history) - window]


# ---------- pipeline -------------------------------------------------------


def update_anchor(scratch, mid):
    """Expanding-window mean of all observed mids — stable fair-value anchor."""
    n = scratch.get("anchor_n", 0) + 1
    s = scratch.get("anchor_sum", 0.0) + mid
    scratch["anchor_n"] = n
    scratch["anchor_sum"] = s
    return s / n


def update_pnl_and_check_stop(cfg, state, scratch, position, fair):
    """Track cash + mark-to-market PnL; return True if below stop_loss_pnl."""
    stop = cfg.get("stop_loss_pnl", -1e18)
    if stop <= -1e17:
        return False
    cash = scratch.get("cash", 0.0)
    for trades in state.own_trades.values():
        for t in trades:
            if t.symbol != cfg["product"]:
                continue
            if t.buyer == "SUBMISSION":
                cash -= t.price * t.quantity
            elif t.seller == "SUBMISSION":
                cash += t.price * t.quantity
    scratch["cash"] = cash
    scratch["own_pnl"] = cash + position * fair
    return scratch["own_pnl"] < stop


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


def divergence_take_orders(cfg, depth, scratch, position, anchor, mid):
    """When mid diverges from anchor by ≥ diverge_threshold, take aggressively."""
    threshold = cfg.get("diverge_threshold", 0)
    if threshold <= 0 or scratch.get("anchor_n", 0) < ANCHOR_WARMUP:
        return [], 0, 0
    diverge = mid - anchor
    if abs(diverge) < threshold:
        return [], 0, 0

    product, limit = cfg["product"], cfg["position_limit"]
    max_pos = cfg.get("max_diverge_position", 60)
    out, bought, sold = [], 0, 0
    if diverge > 0 and position > -max_pos:
        room = position + max_pos
        for price, qty in search_buys(depth):
            cap = min(limit + position - sold, DIVERGE_TAKE_SIZE - sold, room - sold)
            if cap <= 0:
                break
            take = min(qty, cap)
            out.append(Order(product, price, -take))
            sold += take
    elif diverge < 0 and position < max_pos:
        room = max_pos - position
        for price, qty in search_sells(depth):
            cap = min(limit - position - bought, DIVERGE_TAKE_SIZE - bought, room - bought)
            if cap <= 0:
                break
            take = min(qty, cap)
            out.append(Order(product, price, take))
            bought += take
    return out, bought, sold


def take_orders(cfg, depth, fair, position):
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
    product, limit = cfg["product"], cfg["position_limit"]
    qsize = cfg.get("quote_size", 20)
    skew = position * SKEW_PER_UNIT
    bid_px = min(math.floor(fair - c * (fair - best_bid) - skew), best_ask - 1)
    ask_px = max(math.ceil(fair + c * (best_ask - fair) - skew), best_bid + 1)
    buy = max(0, min(qsize, limit - position - bought))
    sell = max(0, min(qsize, limit + position - sold))
    out = []
    if buy > 0 and bid_px < ask_px:
        out.append(Order(product, bid_px, buy))
    if sell > 0 and ask_px > bid_px:
        out.append(Order(product, ask_px, -sell))
    return out


def make_orders(cfg, state, scratch):
    depth = state.order_depths.get(cfg["product"])
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []

    best_bid = max(depth.buy_orders)
    best_ask = min(depth.sell_orders)
    mid = (best_bid + best_ask) / 2
    fair = full_depth_mid(depth)
    fair = adjust_fair_for_aggressor_flow(cfg, fair, best_bid, best_ask, state, scratch)

    anchor = update_anchor(scratch, mid)
    c = vol_widened_spread(cfg, scratch, best_bid, best_ask)
    position = state.position.get(cfg["product"], 0)

    if update_pnl_and_check_stop(cfg, state, scratch, position, fair):
        return []   # stop-loss tripped — pull all quotes for this product

    diverge, d_bought, d_sold = divergence_take_orders(
        cfg, depth, scratch, position, anchor, mid
    )
    pos_eff = position + d_bought - d_sold
    takes, bought, sold = take_orders(cfg, depth, fair, pos_eff)
    bought += d_bought
    sold += d_sold
    quotes = make_quote(cfg, fair, best_bid, best_ask, position, c, bought, sold)
    return diverge + takes + quotes


# ---------- per-product configuration --------------------------------------
# diverge_threshold values are the rounded 95th-percentile of |mid − anchor|
# measured per-product on round-3 historical data (see plot_hydrogel_anchor).
PRODUCTS = [
    {"product": "HYDROGEL_PACK", "position_limit": 200, "aggressor_lambda": -0.010,
     "diverge_threshold": 40, "max_diverge_position": 195, "stop_loss_pnl": -4000},
    {"product": "VELVETFRUIT_EXTRACT", "position_limit": 200, "aggressor_lambda": 0.012,
     "diverge_threshold": 25, "max_diverge_position": 195},
    {"product": "VEV_4000", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5,
     "aggressor_lambda": 0.015, "diverge_threshold": 25, "max_diverge_position": 295},
    {"product": "VEV_4500", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5,
     "diverge_threshold": 25, "max_diverge_position": 295},
    {"product": "VEV_5000", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5,
     "diverge_threshold": 22, "max_diverge_position": 295},
    {"product": "VEV_5100", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5,
     "diverge_threshold": 18, "max_diverge_position": 295},
    {"product": "VEV_5200", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5,
     "diverge_threshold": 14, "max_diverge_position": 295},
    {"product": "VEV_5300", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5,
     "diverge_threshold": 10, "max_diverge_position": 295},
    {"product": "VEV_5400", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.5,
     "diverge_threshold": 5, "max_diverge_position": 295},
    {"product": "VEV_5500", "position_limit": 300, "quote_size": 30, "baseline_vol": 0.3,
     "diverge_threshold": 3, "max_diverge_position": 295},
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
