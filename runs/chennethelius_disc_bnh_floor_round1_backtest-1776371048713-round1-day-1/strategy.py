"""
disc_bnh_floor: Absolute buy-and-hold floor strategy.

Per Discord consensus (kyometzu, anish2216, egor_6809): straight BnH on pepper
yields 7.5-10k. This is the benchmark every strategy must beat.

No market making, no fair value, no signals. Just take asks up to full long
on PEPPER immediately, then hold. OSMIUM ignored (treated as 0 PnL).

Use as: baseline floor for MC comparison.
"""
from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    def bid(self):
        return 15

    def run(self, state: TradingState):
        result: dict[str, list[Order]] = {}
        for symbol in state.order_depths:
            if symbol != "INTARIAN_PEPPER_ROOT":
                result[symbol] = []
                continue
            depth = state.order_depths[symbol]
            pos = state.position.get(symbol, 0)
            lim = self.LIMITS[symbol]
            need = lim - pos
            if need <= 0 or not depth.sell_orders:
                result[symbol] = []
                continue
            orders = []
            for ask in sorted(depth.sell_orders):
                vol = min(-depth.sell_orders[ask], need)
                if vol > 0:
                    orders.append(Order(symbol, ask, vol))
                    need -= vol
                    if need <= 0:
                        break
            result[symbol] = orders
        return result, 0, ""
