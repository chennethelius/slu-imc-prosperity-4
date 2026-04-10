from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List


class Trader:
    """
    Variant D: Best of FrankfurtHedgehogs ideas
    - EMERALDS: unchanged (inventory-shifted FV take + penny-jump)
    - TOMATOES: wall-mid FV + two-tier taking + volume-conditional penny-jump
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

    # EMERALDS: unchanged from baseline
    def _emeralds(self, d, pos):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb, ba = max(d.buy_orders), min(d.sell_orders)
        if bb >= ba:
            return []
        fv, lim = 10000, 80
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

    # TOMATOES: wall-mid FV + two-tier taking + volume-conditional penny-jump
    def _tomatoes(self, symbol, depth, pos):
        if not depth.buy_orders or not depth.sell_orders:
            return []
        bids = sorted(depth.buy_orders.keys(), reverse=True)
        asks = sorted(depth.sell_orders.keys())
        bb, ba = bids[0], asks[0]
        if bb >= ba:
            return []

        # Wall-mid FV: outermost levels midpoint (Bot 1 at FV +/- 8 = exact FV)
        bid_wall = min(depth.buy_orders.keys())
        ask_wall = max(depth.sell_orders.keys())
        fv = (bid_wall + ask_wall) / 2.0

        fv_eff = fv - pos * 0.15
        fvi = int(round(fv_eff))

        lim = 80
        orders = []
        br, sr = lim - pos, lim + pos

        # Two-tier taking: aggressive far from FV, flatten-only near FV
        for a in asks:
            if a < fv_eff - 1 and br > 0:
                v = min(-depth.sell_orders[a], br)
                orders.append(Order(symbol, a, v)); br -= v
            elif a < fv_eff and pos < 0 and br > 0:
                v = min(-depth.sell_orders[a], br, abs(pos))
                if v > 0:
                    orders.append(Order(symbol, a, v)); br -= v
            else:
                break

        for b in bids:
            if b > fv_eff + 1 and sr > 0:
                v = min(depth.buy_orders[b], sr)
                orders.append(Order(symbol, b, -v)); sr -= v
            elif b > fv_eff and pos > 0 and sr > 0:
                v = min(depth.buy_orders[b], sr, pos)
                if v > 0:
                    orders.append(Order(symbol, b, -v)); sr -= v
            else:
                break

        # Volume-conditional penny-jump
        best_bid_vol = depth.buy_orders.get(bb, 0)
        best_ask_vol = abs(depth.sell_orders.get(ba, 0))

        if best_bid_vol > 1:
            bp = min(bb + 1, fvi - 1)
        else:
            bp = min(bb, fvi - 1)

        if best_ask_vol > 1:
            ap = max(ba - 1, fvi + 1)
        else:
            ap = max(ba, fvi + 1)

        if br > 0:
            orders.append(Order(symbol, bp, br))
        if sr > 0:
            orders.append(Order(symbol, ap, -sr))
        return orders
