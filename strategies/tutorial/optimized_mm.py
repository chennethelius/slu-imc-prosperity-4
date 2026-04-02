from datamodel import Order, OrderDepth, TradingState
from typing import List
import json


class Trader:
    """
    Optimal MM with slow EMA fair value + full-capacity penny-jumping.

    1. Take all favorable liquidity (buy below FV, sell above FV)
    2. Zero-edge flatten when position > 10 to free capacity
    3. Full capacity penny-jump (bb+1 / ba-1) with edge guard
    4. Backup layer at FV +/- 3 for remaining capacity

    EMERALDS: fixed FV at 10000
    TOMATOES: slow EMA (alpha=0.027) for stable FV that captures drift
    """

    LIMITS = {"EMERALDS": 80, "TOMATOES": 80}
    FAIR = {"EMERALDS": 10000}
    EMA_ALPHA = 0.027

    def run(self, state: TradingState) -> tuple[dict[str, list[Order]], int, str]:
        try:
            data = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            data = {}
        ema_prices = data.get("ema", {})

        result: dict[str, list[Order]] = {}

        for symbol, depth in state.order_depths.items():
            if symbol not in self.LIMITS:
                result[symbol] = []
                continue

            if not depth.buy_orders or not depth.sell_orders:
                result[symbol] = []
                continue

            pos = state.position.get(symbol, 0)
            limit = self.LIMITS[symbol]

            best_bid = max(depth.buy_orders)
            best_ask = min(depth.sell_orders)
            mid = (best_bid + best_ask) / 2

            if symbol in self.FAIR:
                fv = self.FAIR[symbol]
            else:
                prev_ema = ema_prices.get(symbol, mid)
                fv = self.EMA_ALPHA * mid + (1 - self.EMA_ALPHA) * prev_ema
                ema_prices[symbol] = fv

            result[symbol] = self._trade(symbol, depth, pos, limit, fv, best_bid, best_ask)

        trader_data = json.dumps({"ema": ema_prices})
        return result, 0, trader_data

    def _trade(
        self,
        symbol: str,
        depth: OrderDepth,
        pos: int,
        limit: int,
        fv: float,
        best_bid: int,
        best_ask: int,
    ) -> list[Order]:
        if best_bid >= best_ask:
            return []

        fv_int = int(round(fv))
        orders: list[Order] = []
        buy_room = limit - pos
        sell_room = limit + pos

        # Step 1: Take all favorable liquidity
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

        # Step 2: Zero-edge inventory flattening at low threshold
        if pos > 10 and fv_int in depth.buy_orders and sell_room > 0:
            vol = min(depth.buy_orders[fv_int], sell_room)
            orders.append(Order(symbol, fv_int, -vol))
            sell_room -= vol
        elif pos < -10 and fv_int in depth.sell_orders and buy_room > 0:
            vol = min(-depth.sell_orders[fv_int], buy_room)
            orders.append(Order(symbol, fv_int, vol))
            buy_room -= vol

        # Step 3: Full capacity penny-jump with edge guard
        bid1 = best_bid + 1
        ask1 = best_ask - 1

        if bid1 >= fv_int:
            bid1 = fv_int - 1
        if ask1 <= fv_int:
            ask1 = fv_int + 1

        sz = min(80, buy_room)
        if sz > 0:
            orders.append(Order(symbol, bid1, sz))
            buy_room -= sz
        sz = min(80, sell_room)
        if sz > 0:
            orders.append(Order(symbol, ask1, -sz))
            sell_room -= sz

        # Backup layer for remaining capacity
        if buy_room > 0:
            orders.append(Order(symbol, fv_int - 3, buy_room))
        if sell_room > 0:
            orders.append(Order(symbol, fv_int + 3, -sell_room))

        return orders
