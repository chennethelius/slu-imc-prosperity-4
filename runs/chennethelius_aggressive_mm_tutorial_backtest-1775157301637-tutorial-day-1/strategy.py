from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List


class Trader:
    """
    Spread-capture market maker with inventory management.

    The tutorial market has wide spreads (8-16 ticks). The winning
    strategy is to quote inside the spread and capture it, with
    larger size than the default trader (5 -> 20+).

    Improvements over bundled trader:
    - Bigger quote size (more fills per tick)
    - Multiple price levels (capture more of the book)
    - Inventory skew (shift quotes to flatten when position builds)
    - Fair value anchor (don't quote on the wrong side of true value)
    """

    LIMITS = {"EMERALDS": 80, "TOMATOES": 80}
    FAIR = {"EMERALDS": 10000}  # TOMATOES: use mid

    def run(self, state: TradingState) -> tuple[dict[str, list[Order]], int, str]:
        result: dict[str, list[Order]] = {}

        for symbol, depth in state.order_depths.items():
            if symbol not in self.LIMITS:
                result[symbol] = []
                continue

            pos = state.position.get(symbol, 0)
            limit = self.LIMITS[symbol]
            result[symbol] = self._quote(symbol, depth, pos, limit)

        return result, 0, ""

    def _quote(
        self, symbol: str, depth: OrderDepth, pos: int, limit: int
    ) -> list[Order]:
        if not depth.buy_orders or not depth.sell_orders:
            return []

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        spread = best_ask - best_bid

        if spread <= 0:
            return []

        orders = []

        # Fair value: fixed for EMERALDS, mid for others
        if symbol in self.FAIR:
            fv = self.FAIR[symbol]
        else:
            fv = (best_bid + best_ask) / 2

        # Primary quote: 1 tick inside the spread
        if spread > 1:
            bid1 = best_bid + 1
            ask1 = best_ask - 1
        else:
            bid1 = best_bid
            ask1 = best_ask

        # Inventory skew: push quotes to reduce position
        # When long: lower both prices (encourage selling to us less, buying from us more)
        # When short: raise both prices
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
