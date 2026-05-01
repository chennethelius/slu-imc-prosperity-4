"""
Round 4 — z_take_v2 (TMP).

z_take + three improvements aimed at relieving limit-saturation:

  1. Close-at-mean: when |z| < close_z_thresh AND |pos| > 0, walk the
     book to flatten toward 0 (any acceptable price). Realises the
     reversion profit instead of waiting for an opposite-side z trigger.
  2. Per-product take_size: smaller take leaves headroom for adverse
     moves before saturation.
  3. Selective OTM dampening: deep OTM strikes have weaker static-mean
     fit (per drift/sd analysis), so they get smaller take_size.

All three are exposed as cfg keys so the sweep script can search.
"""

import json
from datamodel import Order, TradingState


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


# Per-product config. take_size now varies: smaller on OTM strikes where
# the static mean is least reliable. close_z_thresh: when |z| drops below
# this and we hold a position, flatten back toward 0.
CFGS = [
    {"symbol": "HYDROGEL_PACK",       "mean": 9994, "sd": 32.588, "z_thresh": 1.0, "close_z_thresh": 0.3, "take_size": 50, "close_size": 50, "limit": 200},
    {"symbol": "VELVETFRUIT_EXTRACT", "mean": 5247, "sd": 17.091, "z_thresh": 1.0, "close_z_thresh": 0.3, "take_size": 50, "close_size": 50, "limit": 200},
    {"symbol": "VEV_4000",            "mean": 1247, "sd": 17.114, "z_thresh": 1.0, "close_z_thresh": 0.3, "take_size": 50, "close_size": 50, "limit": 300},
    {"symbol": "VEV_4500",            "mean":  747, "sd": 17.105, "z_thresh": 1.0, "close_z_thresh": 0.3, "take_size": 50, "close_size": 50, "limit": 300},
    {"symbol": "VEV_5000",            "mean":  252, "sd": 16.381, "z_thresh": 1.0, "close_z_thresh": 0.3, "take_size": 50, "close_size": 50, "limit": 300},
    {"symbol": "VEV_5100",            "mean":  163, "sd": 15.327, "z_thresh": 1.0, "close_z_thresh": 0.3, "take_size": 50, "close_size": 50, "limit": 300},
    {"symbol": "VEV_5200",            "mean":   91, "sd": 12.796, "z_thresh": 1.0, "close_z_thresh": 0.3, "take_size": 30, "close_size": 50, "limit": 300},
    {"symbol": "VEV_5300",            "mean":   43, "sd":  8.976, "z_thresh": 1.0, "close_z_thresh": 0.3, "take_size": 20, "close_size": 50, "limit": 300},
    {"symbol": "VEV_5400",            "mean":   14, "sd":  4.608, "z_thresh": 1.0, "close_z_thresh": 0.3, "take_size": 20, "close_size": 50, "limit": 300},
    {"symbol": "VEV_5500",            "mean":    6, "sd":  2.477, "z_thresh": 1.0, "close_z_thresh": 0.3, "take_size": 20, "close_size": 50, "limit": 300},
]


def _orders(state, cfg):
    sym = cfg["symbol"]
    depth = state.order_depths.get(sym)
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    mid = (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0
    mean, sd = cfg["mean"], cfg["sd"]
    if sd <= 0:
        return []
    z = (mid - mean) / sd
    abs_z = abs(z)

    pos = state.position.get(sym, 0)
    limit = cfg["limit"]

    # 1. Close-at-mean: when reverted past close_z_thresh and we hold a
    #    position, walk the book any-price toward 0.
    if abs_z < cfg["close_z_thresh"] and pos != 0:
        close_size = cfg["close_size"]
        if pos > 0:
            qty = min(pos, close_size, limit + pos)
            if qty > 0:
                orders, _ = _walk_book(depth, -1, sym, lambda px: True, qty)
                return orders
        else:
            qty = min(-pos, close_size, limit - pos)
            if qty > 0:
                orders, _ = _walk_book(depth, +1, sym, lambda px: True, qty)
                return orders
        return []

    # 2. Z-take: standard cross-the-spread when |z| >= z_thresh.
    if abs_z < cfg["z_thresh"]:
        return []

    take_size = cfg["take_size"]
    if z > 0:
        room = max(0, min(take_size, limit + pos))
        if room <= 0:
            return []
        orders, _ = _walk_book(depth, -1, sym, lambda px: px >= mean, room)
        return orders
    room = max(0, min(take_size, limit - pos))
    if room <= 0:
        return []
    orders, _ = _walk_book(depth, +1, sym, lambda px: px <= mean, room)
    return orders


class Trader:
    def bid(self):
        return 0

    def run(self, state: TradingState):
        orders: dict[str, list[Order]] = {}
        for cfg in CFGS:
            ors = _orders(state, cfg)
            if ors:
                orders[cfg["symbol"]] = ors
        return orders, 0, ""
