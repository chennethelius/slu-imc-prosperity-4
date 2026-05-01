import json

from datamodel import Order, TradingState

VEV = "VELVETFRUIT_EXTRACT"


class Trader:
    """Buy one VELVETFRUIT_EXTRACT on the first available ask, print the fill price, then hold."""

    def bid(self):
        return 15

    def run(self, state: TradingState):
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}

        orders: dict[str, list[Order]] = {}

        if td.get("bought_price") is None:
            for trade in state.own_trades.get(VEV, []):
                if trade.buyer == "SUBMISSION":
                    td["bought_price"] = trade.price
                    print(f"Bought 1 {VEV} at {trade.price}")
                    break

        depth = state.order_depths.get(VEV)
        already_have = state.position.get(VEV, 0) >= 1
        order_pending = td.get("ordered", False) and td.get("bought_price") is None

        if not already_have and not order_pending and depth and depth.sell_orders:
            best_ask = min(depth.sell_orders)
            orders[VEV] = [Order(VEV, best_ask, 1)]
            td["ordered"] = True

        return orders, 0, json.dumps(td)