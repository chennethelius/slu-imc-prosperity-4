import json
import numpy as np
from datamodel import OrderDepth, TradingState, Order
from typing import List

POSITION_LIMITS = {
    "EMERALDS": 80,
    "TOMATOES": 80,
}

# ── EMERALDS: market-making config ───────────────────────────────────────────
EMR_FAIR = 10000
EMR_SPREAD = 7
EMR_QUOTE_SIZE = 25

# ── TOMATOES: market-making config ───────────────────────────────────────────
TOM_SPREAD = 6
TOM_QUOTE_SIZE = 25
TOM_HISTORY_LEN = 20  # number of ticks of mid prices to keep


def mid_price(order_depth: OrderDepth) -> float | None:
    if not order_depth.buy_orders or not order_depth.sell_orders:
        return None
    best_bid = max(order_depth.buy_orders)
    best_ask = min(order_depth.sell_orders)
    bid_vol = order_depth.buy_orders[best_bid]
    ask_vol = abs(order_depth.sell_orders[best_ask])
    return (best_bid * ask_vol + best_ask * bid_vol) / (bid_vol + ask_vol)


def rolling_fair_value(mid: float, history: list) -> float:
    """Exponentially weighted average over recent mid prices."""
    history.append(mid)
    if len(history) > TOM_HISTORY_LEN:
        history.pop(0)
    if len(history) == 1:
        return mid
    prices = np.array(history)
    weights = np.exp(np.linspace(-1, 0, len(prices)))
    return float(np.average(prices, weights=weights))


def market_make(product: str, order_depth: OrderDepth, position: int,
                fair: float, spread: int, quote_size: int) -> List[Order]:
    limit = POSITION_LIMITS[product]
    pos = position
    orders: List[Order] = []

    # Sweep mispriced orders
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

    # Passive quotes with position skew
    skew = pos / limit
    buy_qty = min(max(1, round(quote_size * (1 - skew))), max(0, limit - pos))
    sell_qty = min(max(1, round(quote_size * (1 + skew))), max(0, limit + pos))

    if buy_qty > 0:
        orders.append(Order(product, round(fair) - spread, buy_qty))
    if sell_qty > 0:
        orders.append(Order(product, round(fair) + spread, -sell_qty))

    return orders


class Trader:

    def bid(self):
        return 15

    def run(self, state: TradingState):
        result = {}

        try:
            trader_state = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            trader_state = {}

        tom_history = trader_state.get("tom_history", [])

        for product, order_depth in state.order_depths.items():
            position = int(state.position.get(product, 0))

            if product == "EMERALDS":
                result[product] = market_make(
                    product, order_depth, position,
                    EMR_FAIR, EMR_SPREAD, EMR_QUOTE_SIZE
                )
            elif product == "TOMATOES":
                mid = mid_price(order_depth)
                if mid is None:
                    result[product] = []
                else:
                    fair = rolling_fair_value(mid, tom_history)
                    result[product] = market_make(
                        product, order_depth, position,
                        fair, TOM_SPREAD, TOM_QUOTE_SIZE
                    )
            else:
                result[product] = []

        trader_data = json.dumps({"tom_history": tom_history})
        return result, 0, trader_data