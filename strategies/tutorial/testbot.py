from datamodel import OrderDepth, TradingState, Order
from typing import List

POSITION_LIMITS = {
    "EMERALDS": 80,
    "TOMATOES": 80,
}

# Hardcoded fair values; None = estimate from order book mid
FAIR_VALUES = {
    "EMERALDS": 10000,
    "TOMATOES": None,
}

SPREAD = {
    "EMERALDS": 7,
    "TOMATOES": 6,
}

QUOTE_SIZE = 10


def estimate_fair_value(order_depth: OrderDepth) -> float | None:
    if not order_depth.buy_orders or not order_depth.sell_orders:
        return None
    best_bid = max(order_depth.buy_orders)
    best_ask = min(order_depth.sell_orders)
    return (best_bid + best_ask) / 2


class Trader:

    def run(self, state: TradingState):
        result = {}

        for product, order_depth in state.order_depths.items():
            if product not in POSITION_LIMITS:
                result[product] = []
                continue

            limit = POSITION_LIMITS[product]
            position = int(state.position.get(product, 0))
            orders: List[Order] = []

            fair = FAIR_VALUES.get(product) or estimate_fair_value(order_depth)
            if fair is None:
                result[product] = []
                continue

            spread = SPREAD.get(product, 2)

            # Track position locally as we add orders
            pos = position

            # ── Sweep: take mispriced resting orders ──────────────────────────
            for ask_price, ask_amount in sorted(order_depth.sell_orders.items()):
                if ask_price >= fair:
                    break
                qty = min(-ask_amount, max(0, limit - pos))
                if qty <= 0:
                    break
                orders.append(Order(product, ask_price, qty))
                pos += qty

            for bid_price, bid_amount in sorted(order_depth.buy_orders.items(), reverse=True):
                if bid_price <= fair:
                    break
                qty = min(bid_amount, max(0, limit + pos))
                if qty <= 0:
                    break
                orders.append(Order(product, bid_price, -qty))
                pos -= qty

            # ── Quote: post passive bid/ask with remaining capacity ───────────
            # Skew size toward flat: long = prefer selling, short = prefer buying
            skew = pos / limit  # -1.0 to +1.0
            buy_qty = min(round(QUOTE_SIZE * (1 - skew)), max(0, limit - pos))
            sell_qty = min(round(QUOTE_SIZE * (1 + skew)), max(0, limit + pos))

            bid_price = round(fair) - spread
            ask_price = round(fair) + spread

            if buy_qty > 0:
                orders.append(Order(product, bid_price, buy_qty))
            if sell_qty > 0:
                orders.append(Order(product, ask_price, -sell_qty))

            print(f"[{product}] pos={position:+d} fair={fair} orders={[(o.price, o.quantity) for o in orders]}")
            result[product] = orders

        return result, 0, ""