from datamodel import OrderDepth, Symbol, Listing, Trade, Observation, ProsperityEncoder, TradingState, Order
from typing import Any, Dict, List
import json

class Trader:

    EMERALDS_FAIR_VALUE = 10000
    EMERALDS_POSITION_LIMIT = 80
    EMERALDS_SPREAD = 2
    EMERALDS_SKEW_FACTOR = 0.5

    def run(self, state: TradingState):
        result = {}

        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            if product == "EMERALDS":
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
