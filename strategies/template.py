"""
Prosperity 4 Strategy Template

Copy this file to get started:
    cp strategies/template.py strategies/round1/my_strategy.py

Then edit your strategy and run a backtest:
    cd backtester && make round1 TRADER=../strategies/round1/my_strategy.py

Push to trigger CI and publish results to the team dashboard:
    git push

Folder convention (CI matches strategy folder to dataset):
    strategies/tutorial/   -> runs against tutorial data (EMERALDS, TOMATOES)
    strategies/round1/     -> runs against round1 data
    strategies/round2/     -> runs against round2 data
    ...

Position limits — check CLAUDE.md for the full table.
"""

from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List
import json


class Trader:
    def bid(self) -> int:
        # Round 2 Market Access Fee: top 50% of bids unlock 125% quote volume
        # (default testing mode runs at 80%); the accepted bid is subtracted
        # from total PnL as a one-time fee. Ignored in all other rounds.
        return 0

    def run(self, state: TradingState) -> tuple[dict[str, List[Order]], int, str]:
        """
        Called once per tick with the current market state.

        Returns:
            orders: dict mapping symbol -> list of Order objects
            conversions: int (for cross-exchange arb products)
            trader_data: str (JSON string persisted to next tick)
        """
        orders: Dict[str, List[Order]] = {}
        conversions = 0

        # Load persisted state from previous tick
        trader_state = {}
        if state.traderData:
            try:
                trader_state = json.loads(state.traderData)
            except json.JSONDecodeError:
                pass

        # Your strategy logic here
        for symbol, order_depth in state.order_depths.items():
            position = state.position.get(symbol, 0)
            orders[symbol] = []

            # Example: simple market maker
            # if order_depth.buy_orders and order_depth.sell_orders:
            #     best_bid = max(order_depth.buy_orders)
            #     best_ask = min(order_depth.sell_orders)
            #     mid = (best_bid + best_ask) / 2
            #     orders[symbol].append(Order(symbol, int(mid) - 1, 5))   # buy
            #     orders[symbol].append(Order(symbol, int(mid) + 1, -5))  # sell

        # Persist state to next tick
        trader_data = json.dumps(trader_state)

        return orders, conversions, trader_data
