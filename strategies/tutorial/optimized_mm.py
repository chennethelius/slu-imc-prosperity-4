from datamodel import Order, OrderDepth, TradingState
from typing import List


class Trader:
    """
    Optimal MM for stable-FV products.

    1. Take all favorable liquidity (buy below FV, sell above FV)
    2. Penny-jump: overbid best bid, undercut best ask (while keeping edge)
    3. If inventory too skewed, flatten at FV to free capacity
    """

    LIMITS = {"EMERALDS": 80, "TOMATOES": 80}
    FAIR = {"EMERALDS": 10000}  # TOMATOES: use wall mid
    INVENTORY_FLATTEN_THRESHOLD = 80

    def run(self, state: TradingState) -> tuple[dict[str, list[Order]], int, str]:
        result: dict[str, list[Order]] = {}

        for symbol, depth in state.order_depths.items():
            if symbol not in self.LIMITS:
                result[symbol] = []
                continue

            pos = state.position.get(symbol, 0)
            limit = self.LIMITS[symbol]
            result[symbol] = self._trade(symbol, depth, pos, limit)

        return result, 0, ""

    def _trade(
        self, symbol: str, depth: OrderDepth, pos: int, limit: int
    ) -> list[Order]:
        if not depth.buy_orders or not depth.sell_orders:
            return []

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)

        if best_bid >= best_ask:
            return []

        # Fair value: fixed for EMERALDS, wall mid for TOMATOES
        if symbol in self.FAIR:
            fv = self.FAIR[symbol]
        else:
            fv = (best_bid + best_ask) / 2

        orders: list[Order] = []
        buy_room = limit - pos
        sell_room = limit + pos

        # Step 1: Take all favorable liquidity across all price levels
        # Buy everything offered below FV
        for ask_price in sorted(depth.sell_orders.keys()):
            if ask_price < fv and buy_room > 0:
                vol = min(-depth.sell_orders[ask_price], buy_room)
                orders.append(Order(symbol, ask_price, vol))
                buy_room -= vol
            else:
                break

        # Sell into everything bid above FV
        for bid_price in sorted(depth.buy_orders.keys(), reverse=True):
            if bid_price > fv and sell_room > 0:
                vol = min(depth.buy_orders[bid_price], sell_room)
                orders.append(Order(symbol, bid_price, -vol))
                sell_room -= vol
            else:
                break

        # Step 2: If inventory too skewed, flatten at FV to free capacity
        fv_int = int(round(fv))
        if pos > self.INVENTORY_FLATTEN_THRESHOLD and sell_room > 0:
            flatten_qty = min(pos - self.INVENTORY_FLATTEN_THRESHOLD, sell_room)
            if flatten_qty > 0:
                orders.append(Order(symbol, fv_int, -flatten_qty))
                sell_room -= flatten_qty
        elif pos < -self.INVENTORY_FLATTEN_THRESHOLD and buy_room > 0:
            flatten_qty = min(-pos - self.INVENTORY_FLATTEN_THRESHOLD, buy_room)
            if flatten_qty > 0:
                orders.append(Order(symbol, fv_int, flatten_qty))
                buy_room -= flatten_qty

        # Step 3: Passive quotes — penny-jump existing liquidity
        # Overbid the best bid by 1, undercut the best ask by 1
        # But never cross fair value (maintain positive edge)
        bid_price = best_bid + 1
        ask_price = best_ask - 1

        # Ensure positive edge: don't bid at or above FV, don't ask at or below FV
        if bid_price >= fv_int:
            bid_price = fv_int - 1
        if ask_price <= fv_int:
            ask_price = fv_int + 1

        # Post full remaining capacity
        if buy_room > 0 and bid_price > 0:
            orders.append(Order(symbol, bid_price, buy_room))
        if sell_room > 0:
            orders.append(Order(symbol, ask_price, -sell_room))

        return orders
