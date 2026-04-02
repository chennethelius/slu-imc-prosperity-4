from datamodel import Order, OrderDepth, TradingState
from typing import List


class Trader:
    """
    Optimized MM v5: Since backtester fills all crossing orders at queue_pen=1.0,
    maximize edge per fill by quoting wider when possible and still getting filled.
    Also take all mispriced orders for guaranteed profit.
    """

    LIMITS = {"EMERALDS": 80, "TOMATOES": 80}
    FAIR = {"EMERALDS": 10000}

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
        spread = best_ask - best_bid

        if spread <= 0:
            return []

        orders: list[Order] = []

        if symbol in self.FAIR:
            fv = self.FAIR[symbol]
        else:
            fv = (best_bid + best_ask) / 2

        buy_room = limit - pos
        sell_room = limit + pos

        # Phase 1: Take ALL mispriced liquidity across all levels
        for ask_price in sorted(depth.sell_orders.keys()):
            if ask_price < fv and buy_room > 0:
                vol = min(-depth.sell_orders[ask_price], buy_room)
                orders.append(Order(symbol, ask_price, vol))
                buy_room -= vol
            else:
                break

        for bid_price in sorted(depth.buy_orders.keys(), reverse=True):
            if bid_price > fv and sell_room > 0:
                vol = min(depth.buy_orders[bid_price], sell_room)
                orders.append(Order(symbol, bid_price, -vol))
                sell_room -= vol
            else:
                break

        # Phase 2: Inventory skew
        skew = 0
        if abs(pos) > 20:
            skew = -1 if pos > 0 else 1
        if abs(pos) > 50:
            skew = -2 if pos > 0 else 2

        # Quote inside spread — maximize edge by quoting at best price
        # that still gets us filled. With spread > 1, penny-jump.
        if spread > 1:
            bid1 = best_bid + 1 + skew
            ask1 = best_ask - 1 + skew
        else:
            bid1 = best_bid + skew
            ask1 = best_ask + skew

        if bid1 >= ask1:
            mid = (bid1 + ask1) // 2
            bid1 = mid
            ask1 = mid + 1

        # Single large layer — since all crossing orders fill anyway,
        # concentrating at the tightest price maximizes PnL per fill
        sz = min(40, buy_room)
        if sz > 0:
            orders.append(Order(symbol, bid1, sz))
            buy_room -= sz
        sz = min(40, sell_room)
        if sz > 0:
            orders.append(Order(symbol, ask1, -sz))
            sell_room -= sz

        # Backup layer at best bid/ask for remaining capacity
        if spread > 2:
            sz = min(30, buy_room)
            if sz > 0:
                orders.append(Order(symbol, best_bid, sz))
                buy_room -= sz
            sz = min(30, sell_room)
            if sz > 0:
                orders.append(Order(symbol, best_ask, -sz))
                sell_room -= sz

        # Catch-all: remaining capacity deeper
        if buy_room > 0:
            orders.append(Order(symbol, best_bid - 1 + skew, buy_room))
        if sell_room > 0:
            orders.append(Order(symbol, best_ask + 1 + skew, -sell_room))

        return orders
