from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List


class Trader:
    """
    Hybrid strategy:
    - EMERALDS: aggressive take + full-capacity penny-jump (FV=10000)
    - TOMATOES: all-level microprice take + penny-jump with inventory control
    """

    LIMITS = {"EMERALDS": 80, "TOMATOES": 80}

    def run(self, state: TradingState) -> tuple[dict[str, list[Order]], int, str]:
        result: dict[str, list[Order]] = {}

        for symbol, depth in state.order_depths.items():
            if symbol not in self.LIMITS:
                result[symbol] = []
                continue

            pos = state.position.get(symbol, 0)

            if symbol == "EMERALDS":
                result[symbol] = self._emeralds(depth, pos)
            elif symbol == "TOMATOES":
                result[symbol] = self._tomatoes(symbol, depth, pos)
            else:
                result[symbol] = []

        return result, 0, ""

    # ── EMERALDS: aggressive take + penny-jump with inventory-shifted FV ──
    def _emeralds(self, d, pos):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb, ba = max(d.buy_orders), min(d.sell_orders)
        if bb >= ba:
            return []
        fv, lim = 10000, 80

        # Inventory-shifted effective FV: encourages position reduction
        fv_eff = fv - pos * 0.15
        fvi = int(round(fv_eff))

        orders = []
        br, sr = lim - pos, lim + pos

        for a in sorted(d.sell_orders):
            if a < fv_eff and br > 0:
                v = min(-d.sell_orders[a], br)
                orders.append(Order("EMERALDS", a, v)); br -= v
            else:
                break
        for b in sorted(d.buy_orders, reverse=True):
            if b > fv_eff and sr > 0:
                v = min(d.buy_orders[b], sr)
                orders.append(Order("EMERALDS", b, -v)); sr -= v
            else:
                break

        bp = min(bb + 1, fvi - 1)
        ap = max(ba - 1, fvi + 1)
        if br > 0:
            orders.append(Order("EMERALDS", bp, br))
        if sr > 0:
            orders.append(Order("EMERALDS", ap, -sr))
        return orders

    # ── TOMATOES: all-level microprice take + penny-jump with inventory control ──
    def _tomatoes(self, symbol, depth, pos):
        if not depth.buy_orders or not depth.sell_orders:
            return []
        bids = sorted(depth.buy_orders.keys(), reverse=True)
        asks = sorted(depth.sell_orders.keys())
        bb, ba = bids[0], asks[0]
        if bb >= ba:
            return []

        # All-level microprice: cross-weighted VWAP for FV estimation
        total_bid_pv = sum(p * depth.buy_orders[p] for p in bids)
        total_bid_v = sum(depth.buy_orders[p] for p in bids)
        total_ask_pv = sum(p * abs(depth.sell_orders[p]) for p in asks)
        total_ask_v = sum(abs(depth.sell_orders[p]) for p in asks)
        vwap_bid = total_bid_pv / total_bid_v
        vwap_ask = total_ask_pv / total_ask_v
        fv = (vwap_bid * total_ask_v + vwap_ask * total_bid_v) / (total_bid_v + total_ask_v)

        # Inventory-adjusted FV: shift to encourage position reduction
        fv_eff = fv - pos * 0.15
        fvi = int(round(fv_eff))

        lim = 80
        orders = []
        br, sr = lim - pos, lim + pos

        for a in asks:
            if a < fv_eff and br > 0:
                v = min(-depth.sell_orders[a], br)
                orders.append(Order(symbol, a, v)); br -= v
            else:
                break
        for b in bids:
            if b > fv_eff and sr > 0:
                v = min(depth.buy_orders[b], sr)
                orders.append(Order(symbol, b, -v)); sr -= v
            else:
                break

        bp = min(bb + 1, fvi - 1)
        ap = max(ba - 1, fvi + 1)
        if br > 0:
            orders.append(Order(symbol, bp, br))
        if sr > 0:
            orders.append(Order(symbol, ap, -sr))
        return orders
