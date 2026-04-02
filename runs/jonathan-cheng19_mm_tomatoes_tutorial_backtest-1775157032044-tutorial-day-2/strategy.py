from datamodel import OrderDepth, Symbol, Listing, Trade, Observation, ProsperityEncoder, TradingState, Order
from typing import Any, Dict, List
import json

class Trader:

    TOMATOES_POSITION_LIMIT = 80
    SPREAD = 2

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

                bid_price = int(round(mid_price - self.SPREAD))
                ask_price = int(round(mid_price + self.SPREAD))

                position = state.position.get(product, 0)
                buy_capacity = self.TOMATOES_POSITION_LIMIT - position
                sell_capacity = self.TOMATOES_POSITION_LIMIT + position

                if buy_capacity > 0:
                    orders.append(Order(product, bid_price, buy_capacity))
                if sell_capacity > 0:
                    orders.append(Order(product, ask_price, -sell_capacity))

            result[product] = orders

        traderData = ""
        conversions = 0
        return result, conversions, traderData
