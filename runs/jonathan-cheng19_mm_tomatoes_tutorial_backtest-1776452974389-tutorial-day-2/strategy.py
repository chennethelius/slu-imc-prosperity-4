from datamodel import OrderDepth, Symbol, Listing, Trade, Observation, ProsperityEncoder, TradingState, Order
from typing import Any, Dict, List
import json

class Trader:

    TOMATOES_POSITION_LIMIT = 80
    TOMATOES_SPREAD = 2

    EMERALDS_FAIR_VALUE = 10000
    EMERALDS_POSITION_LIMIT = 80
    EMERALDS_SPREAD = 2
    EMERALDS_SKEW_FACTOR = 0.5

    def run(self, state: TradingState):
        result = {}

        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            if product == "TOMATOES":
                if not order_depth.buy_orders or not order_depth.sell_orders:
                    result[product] = orders
                    continue

                best_bid = max(order_depth.buy_orders.keys())
                best_ask = min(order_depth.sell_orders.keys())
                mid_price = (best_bid + best_ask) / 2

                bid_price = int(round(mid_price - self.TOMATOES_SPREAD))
                ask_price = int(round(mid_price + self.TOMATOES_SPREAD))

                position = state.position.get(product, 0)
                buy_capacity = self.TOMATOES_POSITION_LIMIT - position
                sell_capacity = self.TOMATOES_POSITION_LIMIT + position

                if buy_capacity > 0:
                    orders.append(Order(product, bid_price, buy_capacity))
                if sell_capacity > 0:
                    orders.append(Order(product, ask_price, -sell_capacity))

            elif product == "EMERALDS":
                position = state.position.get(product, 0)
                limit = self.EMERALDS_POSITION_LIMIT

                # Skew quotes away from building more inventory:
                # positive position → lower both prices to encourage selling
                # negative position → raise both prices to encourage buying
                skew = -round(position * self.EMERALDS_SKEW_FACTOR / limit)

                bid_price = self.EMERALDS_FAIR_VALUE - self.EMERALDS_SPREAD + skew
                ask_price = self.EMERALDS_FAIR_VALUE + self.EMERALDS_SPREAD + skew

                buy_capacity = limit - position
                sell_capacity = limit + position

                # Aggressively take any mispriced orders first
                if order_depth.sell_orders:
                    for ask, vol in sorted(order_depth.sell_orders.items()):
                        if ask < self.EMERALDS_FAIR_VALUE and buy_capacity > 0:
                            take_qty = min(-vol, buy_capacity)
                            orders.append(Order(product, ask, take_qty))
                            buy_capacity -= take_qty

                if order_depth.buy_orders:
                    for bid, vol in sorted(order_depth.buy_orders.items(), reverse=True):
                        if bid > self.EMERALDS_FAIR_VALUE and sell_capacity > 0:
                            take_qty = min(vol, sell_capacity)
                            orders.append(Order(product, bid, -take_qty))
                            sell_capacity -= take_qty

                # Post remaining capacity as passive maker orders
                if buy_capacity > 0:
                    orders.append(Order(product, bid_price, buy_capacity))
                if sell_capacity > 0:
                    orders.append(Order(product, ask_price, -sell_capacity))

            result[product] = orders

        traderData = ""
        conversions = 0
        return result, conversions, traderData
