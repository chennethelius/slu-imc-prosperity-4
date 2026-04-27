from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List


class Trader:
    """
    Hybrid strategy:
    - EMERALDS: aggressive take + full-capacity penny-jump (FV=10000)
    - TOMATOES: spread-capture MM with inventory skew (mid-price FV)
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

    # ── EMERALDS: aggressive take + full-capacity penny-jump ──
    def _emeralds(self, d, pos):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb, ba = max(d.buy_orders), min(d.sell_orders)
        if bb >= ba:
            return []
        fv, lim = 10000, 80
        orders = []
        br, sr = lim - pos, lim + pos
        for a in sorted(d.sell_orders):
            if a < fv and br > 0:
                v = min(-d.sell_orders[a], br)
                orders.append(Order("EMERALDS", a, v)); br -= v
            else:
                break
        for b in sorted(d.buy_orders, reverse=True):
            if b > fv and sr > 0:
                v = min(d.buy_orders[b], sr)
                orders.append(Order("EMERALDS", b, -v)); sr -= v
            else:
                break
        bp, ap = min(bb + 1, fv - 1), max(ba - 1, fv + 1)
        if br > 0:
            orders.append(Order("EMERALDS", bp, br))
        if sr > 0:
            orders.append(Order("EMERALDS", ap, -sr))
        return orders

    # ── TOMATOES: spread-capture MM with inventory skew (unchanged) ──
    def _tomatoes(self, symbol, depth, pos):
        if not depth.buy_orders or not depth.sell_orders:
            return []

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        spread = best_ask - best_bid
        limit = 80

        if spread <= 0:
            return []

        orders = []
        fv = (best_bid + best_ask) / 2

        # Primary quote: 1 tick inside the spread
        if spread > 1:
            bid1 = best_bid + 1
            ask1 = best_ask - 1
        else:
            bid1 = best_bid
            ask1 = best_ask

        # Inventory skew: push quotes to reduce position
        skew = 0
        if abs(pos) > 20:
            skew = -1 if pos > 0 else 1
        if abs(pos) > 50:
            skew = -2 if pos > 0 else 2

        bid1 += skew
        ask1 += skew

        buy_room = limit - pos
        sell_room = limit + pos

        # Layer 1: tight quote, big size
        size1 = 20
        if buy_room > 0:
            qty = min(size1, buy_room)
            orders.append(Order(symbol, bid1, qty))
            buy_room -= qty
        if sell_room > 0:
            qty = min(size1, sell_room)
            orders.append(Order(symbol, ask1, -qty))
            sell_room -= qty

        # Layer 2: at the join (match best bid/ask), fill more
        if spread > 2:
            size2 = 15
            if buy_room > 0:
                orders.append(Order(symbol, best_bid, min(size2, buy_room)))
                buy_room -= min(size2, buy_room)
            if sell_room > 0:
                orders.append(Order(symbol, best_ask, -min(sell_room, size2)))
                sell_room -= min(sell_room, size2)

        # Layer 3: deeper, catch large moves
        if spread > 6 and buy_room > 0:
            orders.append(Order(symbol, best_bid - 1, min(20, buy_room)))
        if spread > 6 and sell_room > 0:
            orders.append(Order(symbol, best_ask + 1, -min(20, sell_room)))

        return orders
