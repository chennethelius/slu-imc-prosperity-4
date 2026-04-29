"""
Round 5 — pure market making on the MM-friendly product cluster.

Why this strategy:
  Round 5 daily price levels are random walks (Hurst ≈ 0.50, ADF can't reject
  unit root for any of the 50 products; day-over-day return correlation is
  -0.18 — anti-momentum if anything). No directional edge exists. The only
  durable edge in a random-walk market is collecting bid-ask spread, with
  inventory control to bound risk.

Selection rules (from data analysis on round 5 days 2-4):
  - Tight spread (avg < 18) AND low CV (< 6%)
  - Excludes high-volatility groups (PEBBLES, MICROCHIP) where adverse
    selection on big-mover days (PEBBLES_XL +37%, MICROCHIP_OVAL -25%)
    would torch the position.

Per-tick logic per traded product:
  1. Compute microprice (volume-weighted between best bid and ask).
  2. Skew the fair value by inventory — push fair down when long so quotes
     lean toward selling, up when short to lean toward buying.
  3. Target half-spread = max(min_half_spread, observed_spread / 4) so we
     widen automatically when the book is wide.
  4. Quote symmetrically around skewed fair, sized by remaining capacity
     against the position limit.

What this is NOT trying to do:
  - No directional bets (data shows zero predictability)
  - No pairs / cointegration (0/100 within-group pairs cointegrate stably)
  - No momentum (long-top-short-bottom strategy lost 4.6% out of sample)
"""

import json
import math
from typing import Any

from datamodel import (
    Listing, Observation, Order, OrderDepth,
    ProsperityEncoder, Symbol, Trade, TradingState,
)


# Position limit assumed conservative; the engine rejects over-limit orders
# anyway, so the cap here only governs how much we *try* to deploy.
POS_LIMIT = 50

# Per-product MM config. min_half is the minimum half-spread we'll quote
# (in seashells). size is the quote depth per side. inv_skew is how many
# seashells we shift fair value per unit of inventory (full position →
# inv_skew shift). All values come from the round 5 spread/CV analysis.
CFG: dict[str, dict] = {
    # SNACKPACK group: avg spread ~16, CV ~1.5-2%. Tightest MM cluster.
    "SNACKPACK_RASPBERRY":         {"size": 8, "min_half": 4, "inv_skew": 6},
    "SNACKPACK_VANILLA":           {"size": 8, "min_half": 4, "inv_skew": 6},
    "SNACKPACK_PISTACHIO":         {"size": 8, "min_half": 4, "inv_skew": 6},
    "SNACKPACK_CHOCOLATE":         {"size": 8, "min_half": 4, "inv_skew": 6},
    "SNACKPACK_STRAWBERRY":        {"size": 8, "min_half": 4, "inv_skew": 6},
    # TRANSLATOR group: spread ~9, CV ~5%. Smaller spread → smaller min_half.
    "TRANSLATOR_ECLIPSE_CHARCOAL": {"size": 6, "min_half": 2, "inv_skew": 4},
    "TRANSLATOR_ASTRO_BLACK":      {"size": 6, "min_half": 2, "inv_skew": 4},
    "TRANSLATOR_GRAPHITE_MIST":    {"size": 6, "min_half": 2, "inv_skew": 4},
    # ROBOT (stable subset only — IRONING/MOPPING were >20% movers).
    "ROBOT_VACUUMING":             {"size": 6, "min_half": 2, "inv_skew": 4},
    "ROBOT_DISHES":                {"size": 6, "min_half": 2, "inv_skew": 4},
    "ROBOT_LAUNDRY":               {"size": 6, "min_half": 2, "inv_skew": 4},
    # SLEEP_POD subset (LAMB_WOOL is the stable one in the group).
    "SLEEP_POD_LAMB_WOOL":         {"size": 6, "min_half": 3, "inv_skew": 4},
    # PANEL_4X4: tight, stable; rest of PANEL was a >20% mover.
    "PANEL_4X4":                   {"size": 6, "min_half": 2, "inv_skew": 4},
    # GALAXY_SOUNDS_DARK_MATTER: tightest in the group.
    "GALAXY_SOUNDS_DARK_MATTER":   {"size": 6, "min_half": 3, "inv_skew": 4},
}


def microprice(od: OrderDepth) -> float | None:
    """Volume-weighted price between best bid and best ask.

    More accurate than simple mid because it shifts toward the side with
    less depth — i.e., if there's only 1 unit on the bid and 50 on the
    ask, the next print is more likely near the bid, so fair sits there.
    Returns None if either side of the book is empty.
    """
    if not od.buy_orders or not od.sell_orders:
        return None
    best_bid = max(od.buy_orders.keys())
    best_ask = min(od.sell_orders.keys())
    bid_vol = od.buy_orders[best_bid]
    ask_vol = abs(od.sell_orders[best_ask])
    total = bid_vol + ask_vol
    if total <= 0:
        return (best_bid + best_ask) / 2.0
    return (best_bid * ask_vol + best_ask * bid_vol) / total


class Trader:
    def bid(self) -> int:
        # Round 2 manual market-access auction (irrelevant in round 5, but
        # the harness requires this method on every Trader class).
        return 0

    def run(self, state: TradingState) -> tuple[dict[str, list[Order]], int, str]:
        orders: dict[str, list[Order]] = {}

        for sym, cfg in CFG.items():
            od = state.order_depths.get(sym)
            if od is None:
                continue
            fair = microprice(od)
            if fair is None:
                continue
            best_bid = max(od.buy_orders.keys())
            best_ask = min(od.sell_orders.keys())
            book_spread = best_ask - best_bid
            if book_spread <= 0:
                continue

            position = state.position.get(sym, 0)

            # Inventory skew: shift fair toward the side we want to fill on.
            # Positive position → push fair down → our ask gets more aggressive,
            # our bid pulls back. Symmetric for shorts.
            inv_shift = cfg["inv_skew"] * position / POS_LIMIT
            skewed_fair = fair - inv_shift

            # Half-spread = at least min_half, but widen if the book is wide
            # (avoids quoting inside a 1-tick book and getting picked off).
            half = max(cfg["min_half"], book_spread / 4.0)

            our_bid_px = math.floor(skewed_fair - half)
            our_ask_px = math.ceil(skewed_fair + half)

            # Don't cross our own quotes; leave at least a 1-tick spread.
            if our_ask_px <= our_bid_px:
                our_ask_px = our_bid_px + 1

            # Capacity: max we can buy = limit - long_position; max we can sell
            # = limit + position. Engine REJECTS ALL orders for this symbol if
            # any of them would breach, so we cap each leg explicitly.
            buy_capacity = POS_LIMIT - position
            sell_capacity = POS_LIMIT + position

            buy_qty = min(cfg["size"], max(0, buy_capacity))
            sell_qty = min(cfg["size"], max(0, sell_capacity))

            ords: list[Order] = []
            if buy_qty > 0:
                ords.append(Order(sym, our_bid_px, buy_qty))
            if sell_qty > 0:
                ords.append(Order(sym, our_ask_px, -sell_qty))

            if ords:
                orders[sym] = ords

        logger.flush(state, orders, 0, "")
        return orders, 0, ""


# --------------------------------------------------------------------- Logger
# Boilerplate required for the visualizer to render the run. Truncates state
# fields to fit the 3750-char per-tick budget the harness enforces.
class Logger:
    def __init__(self) -> None:
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects: Any, sep: str = " ", end: str = "\n") -> None:
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state: TradingState, orders: dict[Symbol, list[Order]],
              conversions: int, trader_data: str) -> None:
        base_length = len(self.to_json([
            self.compress_state(state, ""),
            self.compress_orders(orders), conversions, "", "",
        ]))
        max_item_length = (self.max_log_length - base_length) // 3
        print(self.to_json([
            self.compress_state(state, self.truncate(state.traderData, max_item_length)),
            self.compress_orders(orders), conversions,
            self.truncate(trader_data, max_item_length),
            self.truncate(self.logs, max_item_length),
        ]))
        self.logs = ""

    def compress_state(self, state, trader_data):
        return [state.timestamp, trader_data, self.compress_listings(state.listings),
                self.compress_order_depths(state.order_depths),
                self.compress_trades(state.own_trades),
                self.compress_trades(state.market_trades),
                state.position, self.compress_observations(state.observations)]

    def compress_listings(self, listings):
        return [[l.symbol, l.product, l.denomination] for l in listings.values()]

    def compress_order_depths(self, order_depths):
        return {s: [od.buy_orders, od.sell_orders] for s, od in order_depths.items()}

    def compress_trades(self, trades):
        return [[t.symbol, t.price, t.quantity, t.buyer, t.seller, t.timestamp]
                for arr in trades.values() for t in arr]

    def compress_observations(self, observations):
        co = {}
        for product, obs in observations.conversionObservations.items():
            co[product] = [obs.bidPrice, obs.askPrice, obs.transportFees,
                           obs.exportTariff, obs.importTariff,
                           obs.sugarPrice, obs.sunlightIndex]
        return [observations.plainValueObservations, co]

    def compress_orders(self, orders):
        return [[o.symbol, o.price, o.quantity] for arr in orders.values() for o in arr]

    def to_json(self, value):
        return json.dumps(value, cls=ProsperityEncoder, separators=(",", ":"))

    def truncate(self, value, max_length):
        if len(value) <= max_length:
            return value
        return value[: max_length - 3] + "..."


logger = Logger()
