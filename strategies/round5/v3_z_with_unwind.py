"""
Round 5 v2 — pure z-take, no basket overlay.

v1 added a CHOC+VAN / STRAW+RASP basket-sum signal that lost catastrophically
(-89k RASP on D2, -145k VAN + -57k RASP on D4). The sum being stable in
static stats doesn't make it tradeable: per-leg direction is unpredictable
when only the SUM is constrained, and the round-trip spread cost (~32 ticks)
eats most of the expected edge before the sum reverts.

v2 is just the per-product z-take across all 50 products.

Original v1 docstring:

Round 5 v1 — z-take on every product + Snackpack basket-sum mean-reversion.

Round 5 has 50 products × position limit 10. No counterparty (Mark) data
exists — buyer/seller fields are empty. Strategies that rely on Mark
flow won't work; pure z-score / basket arbitrage will.

Patterns identified from R5 D2-D4 data:

1) SNACKPACK BASKET SUMS are nearly constant — strongest edge.
   CHOCOLATE + VANILLA   mean=19940.67  std=76.20    (0.4% deviation)
   STRAWBERRY + RASPBERRY mean=20784.42  std=331.58  (1.6% deviation)
   When sum > target + buffer: SELL both. When sum < target - buffer:
   BUY both. The pair has zero net-direction exposure (one up + one
   down = approx 0), so the trade is statistical, not directional.

2) Snackpack individual products have very tight std (170-360) and wide
   spreads (15-17). z-take with |z|>=2 captures the mean reversion.

3) PEBBLES_XL is anti-correlated -0.5 with the other 4 pebbles. The
   absolute spread is too volatile to monetize cleanly (std 1300-3000),
   so we just use straight z-take per product.

4) GalaxySounds, SleepPods, Microchips, Robots, UVVisors, Translators,
   Panels, OxygenShakes have ~0 internal correlation. Pure z-take per
   product.

Strategy:
  - Per product: z = (mid - empirical_mean) / empirical_std
  - If |z| >= z_thresh: walk book to limit at favorable prices vs mean
  - Snackpack basket overlay: when CHOC+VAN deviates from 19940 by
    > 80 (~1 sigma), and STRAW+RASP from 20784 by > 350, take both
    legs to limit (in opposing directions for the negative-corr pair).

Position limit per product is 10 (tiny). All sizes capped accordingly.
"""

import json
from typing import Any
from datamodel import Listing, Observation, Order, OrderDepth, ProsperityEncoder, Symbol, Trade, TradingState


class Logger:
    def __init__(self) -> None:
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects: Any, sep: str = " ", end: str = "\n") -> None:
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state: TradingState, orders: dict[Symbol, list[Order]], conversions: int, trader_data: str) -> None:
        base_length = len(self.to_json([self.compress_state(state, ""), self.compress_orders(orders), conversions, "", ""]))
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
                self.compress_order_depths(state.order_depths), self.compress_trades(state.own_trades),
                self.compress_trades(state.market_trades), state.position, self.compress_observations(state.observations)]

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
                           obs.exportTariff, obs.importTariff, obs.sugarPrice, obs.sunlightIndex]
        return [observations.plainValueObservations, co]

    def compress_orders(self, orders):
        return [[o.symbol, o.price, o.quantity] for arr in orders.values() for o in arr]

    def to_json(self, value):
        return json.dumps(value, cls=ProsperityEncoder, separators=(",", ":"))

    def truncate(self, value, max_length):
        lo, hi = 0, min(len(value), max_length)
        out = ""
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = value[:mid]
            if len(candidate) < len(value):
                candidate += "..."
            if len(json.dumps(candidate)) <= max_length:
                out = candidate; lo = mid + 1
            else:
                hi = mid - 1
        return out


logger = Logger()


# ============================================================================
# Per-product config — empirical mean & std from R5 D2/D3/D4 (30k ticks each)
# ============================================================================
LIMIT = 10  # all products in R5 have a 10-position limit

CFGS = [
    # GalaxySounds
    {"sym": "GALAXY_SOUNDS_DARK_MATTER",      "mean": 10227, "sd": 331,  "z_thresh": 2.0},
    {"sym": "GALAXY_SOUNDS_BLACK_HOLES",      "mean": 11467, "sd": 958,  "z_thresh": 2.0},
    {"sym": "GALAXY_SOUNDS_PLANETARY_RINGS",  "mean": 10767, "sd": 766,  "z_thresh": 2.0},
    {"sym": "GALAXY_SOUNDS_SOLAR_WINDS",      "mean": 10438, "sd": 541,  "z_thresh": 2.0},
    {"sym": "GALAXY_SOUNDS_SOLAR_FLAMES",     "mean": 11093, "sd": 450,  "z_thresh": 2.0},
    # SleepPods
    {"sym": "SLEEP_POD_SUEDE",                "mean": 11397, "sd": 900,  "z_thresh": 2.0},
    {"sym": "SLEEP_POD_LAMB_WOOL",            "mean": 10701, "sd": 413,  "z_thresh": 2.0},
    {"sym": "SLEEP_POD_POLYESTER",            "mean": 11841, "sd": 978,  "z_thresh": 2.0},
    {"sym": "SLEEP_POD_NYLON",                "mean":  9636, "sd": 509,  "z_thresh": 2.0},
    {"sym": "SLEEP_POD_COTTON",               "mean": 11528, "sd": 888,  "z_thresh": 2.0},
    # Microchips
    {"sym": "MICROCHIP_CIRCLE",               "mean":  9215, "sd": 533,  "z_thresh": 2.0},
    {"sym": "MICROCHIP_OVAL",                 "mean":  8180, "sd": 1552, "z_thresh": 2.0},
    {"sym": "MICROCHIP_SQUARE",               "mean": 13595, "sd": 1830, "z_thresh": 2.0},
    {"sym": "MICROCHIP_RECTANGLE",            "mean":  8732, "sd": 752,  "z_thresh": 2.0},
    {"sym": "MICROCHIP_TRIANGLE",             "mean":  9686, "sd": 833,  "z_thresh": 2.0},
    # Pebbles
    {"sym": "PEBBLES_XS",                     "mean":  7405, "sd": 1450, "z_thresh": 2.0},
    {"sym": "PEBBLES_S",                      "mean":  8932, "sd": 833,  "z_thresh": 2.0},
    {"sym": "PEBBLES_M",                      "mean": 10263, "sd": 688,  "z_thresh": 2.0},
    {"sym": "PEBBLES_L",                      "mean": 10174, "sd": 622,  "z_thresh": 2.0},
    {"sym": "PEBBLES_XL",                     "mean": 13226, "sd": 1777, "z_thresh": 2.0},
    # Robots
    {"sym": "ROBOT_VACUUMING",                "mean":  9167, "sd": 535,  "z_thresh": 2.0},
    {"sym": "ROBOT_MOPPING",                  "mean": 11100, "sd": 767,  "z_thresh": 2.0},
    {"sym": "ROBOT_DISHES",                   "mean": 10018, "sd": 557,  "z_thresh": 2.0},
    {"sym": "ROBOT_LAUNDRY",                  "mean":  9823, "sd": 614,  "z_thresh": 2.0},
    {"sym": "ROBOT_IRONING",                  "mean":  8702, "sd": 771,  "z_thresh": 2.0},
    # UVVisors
    {"sym": "UV_VISOR_YELLOW",                "mean": 10957, "sd": 682,  "z_thresh": 2.0},
    {"sym": "UV_VISOR_AMBER",                 "mean":  7912, "sd": 997,  "z_thresh": 2.0},
    {"sym": "UV_VISOR_ORANGE",                "mean": 10427, "sd": 551,  "z_thresh": 2.0},
    {"sym": "UV_VISOR_RED",                   "mean": 11063, "sd": 588,  "z_thresh": 2.0},
    {"sym": "UV_VISOR_MAGENTA",               "mean": 11112, "sd": 614,  "z_thresh": 2.0},
    # Translators
    {"sym": "TRANSLATOR_SPACE_GRAY",          "mean":  9432, "sd": 503,  "z_thresh": 2.0},
    {"sym": "TRANSLATOR_ASTRO_BLACK",         "mean":  9385, "sd": 490,  "z_thresh": 2.0},
    {"sym": "TRANSLATOR_ECLIPSE_CHARCOAL",    "mean":  9814, "sd": 356,  "z_thresh": 2.0},
    {"sym": "TRANSLATOR_GRAPHITE_MIST",       "mean": 10085, "sd": 500,  "z_thresh": 2.0},
    {"sym": "TRANSLATOR_VOID_BLUE",           "mean": 10859, "sd": 579,  "z_thresh": 2.0},
    # Panels
    {"sym": "PANEL_1X2",                      "mean":  8923, "sd": 590,  "z_thresh": 2.0},
    {"sym": "PANEL_2X2",                      "mean":  9577, "sd": 675,  "z_thresh": 2.0},
    {"sym": "PANEL_1X4",                      "mean":  9398, "sd": 834,  "z_thresh": 2.0},
    {"sym": "PANEL_2X4",                      "mean": 11265, "sd": 627,  "z_thresh": 2.0},
    {"sym": "PANEL_4X4",                      "mean":  9879, "sd": 457,  "z_thresh": 2.0},
    # OxygenShakes
    {"sym": "OXYGEN_SHAKE_MORNING_BREATH",    "mean": 10000, "sd": 653,  "z_thresh": 2.0},
    {"sym": "OXYGEN_SHAKE_EVENING_BREATH",    "mean":  9272, "sd": 400,  "z_thresh": 2.0},
    {"sym": "OXYGEN_SHAKE_MINT",              "mean":  9838, "sd": 508,  "z_thresh": 2.0},
    {"sym": "OXYGEN_SHAKE_CHOCOLATE",         "mean":  9557, "sd": 561,  "z_thresh": 2.0},
    {"sym": "OXYGEN_SHAKE_GARLIC",            "mean": 11926, "sd": 953,  "z_thresh": 2.0},
    # Snackpacks
    {"sym": "SNACKPACK_CHOCOLATE",            "mean":  9843, "sd": 201,  "z_thresh": 1.5},
    {"sym": "SNACKPACK_VANILLA",              "mean": 10097, "sd": 179,  "z_thresh": 1.5},
    {"sym": "SNACKPACK_PISTACHIO",            "mean":  9496, "sd": 187,  "z_thresh": 1.5},
    {"sym": "SNACKPACK_STRAWBERRY",           "mean": 10707, "sd": 364,  "z_thresh": 1.5},
    {"sym": "SNACKPACK_RASPBERRY",            "mean": 10078, "sd": 170,  "z_thresh": 1.5},
]

# Snackpack basket pairs — sum is nearly constant, very stable signal.
BASKET_PAIRS = [
    {"a": "SNACKPACK_CHOCOLATE",  "b": "SNACKPACK_VANILLA",   "sum_mean": 19941, "sum_sd": 76,  "thresh_sd": 1.0},
    {"a": "SNACKPACK_STRAWBERRY", "b": "SNACKPACK_RASPBERRY", "sum_mean": 20784, "sum_sd": 332, "thresh_sd": 1.0},
]


# ============================================================================
# Helpers
# ============================================================================

def _walk_book(depth, side, sym, ok, qty_target):
    """side=+1 takes asks (buys); side=-1 takes bids (sells)."""
    if side > 0:
        prices = sorted(depth.sell_orders); book = depth.sell_orders
    else:
        prices = sorted(depth.buy_orders, reverse=True); book = depth.buy_orders
    out, filled = [], 0
    for px in prices:
        if filled >= qty_target or not ok(px):
            break
        qty = min(abs(book[px]), qty_target - filled)
        if qty <= 0:
            break
        out.append(Order(sym, px, side * qty))
        filled += qty
    return out, filled


UNWIND_Z_THRESH = 0.5  # |z|<this and pos!=0 → walk book to close position


def _z_take(state, cfg, td):
    """v3: z-take + active unwind when |z| reverts toward 0.

    z >= z_thresh → short to limit (favorable px vs mean).
    z <= -z_thresh → long to limit (favorable px vs mean).
    |z| < UNWIND_Z_THRESH AND pos != 0 → walk book to flatten at favorable
        prices vs mean. Locks in mean-reversion profits and frees capacity
        for the next signal.
    """
    sym = cfg["sym"]
    depth = state.order_depths.get(sym)
    if not depth or not depth.buy_orders or not depth.sell_orders:
        return []
    bb = max(depth.buy_orders); ba = min(depth.sell_orders)
    mid = (bb + ba) / 2.0
    mean, sd = cfg["mean"], cfg["sd"]
    if sd <= 0:
        return []
    z = (mid - mean) / sd
    pos = state.position.get(sym, 0)

    # Active unwind: position open AND z reverted near mean
    if abs(z) < UNWIND_Z_THRESH and pos != 0:
        if pos > 0:
            orders, _ = _walk_book(depth, -1, sym, lambda px: px >= mean, abs(pos))
            return orders
        else:
            orders, _ = _walk_book(depth, +1, sym, lambda px: px <= mean, abs(pos))
            return orders

    if abs(z) < cfg["z_thresh"]:
        return []

    if z > 0:
        room = max(0, LIMIT + pos)
        if room <= 0:
            return []
        orders, _ = _walk_book(depth, -1, sym, lambda px: px >= mean, room)
        return orders
    room = max(0, LIMIT - pos)
    if room <= 0:
        return []
    orders, _ = _walk_book(depth, +1, sym, lambda px: px <= mean, room)
    return orders


def _basket_pair_take(state, pair):
    """Snackpack basket sum mean-reversion: when CHOC+VAN sum drifts
    above (target + buffer), short both. Below, long both. Each leg
    takes to its inventory limit at favorable prices vs each leg's mid."""
    a, b = pair["a"], pair["b"]
    da = state.order_depths.get(a); db = state.order_depths.get(b)
    if not da or not db or not da.buy_orders or not da.sell_orders or not db.buy_orders or not db.sell_orders:
        return {}
    bb_a = max(da.buy_orders); ba_a = min(da.sell_orders); mid_a = (bb_a + ba_a) / 2.0
    bb_b = max(db.buy_orders); ba_b = min(db.sell_orders); mid_b = (bb_b + ba_b) / 2.0

    sum_mid = mid_a + mid_b
    z = (sum_mid - pair["sum_mean"]) / max(1.0, pair["sum_sd"])
    if abs(z) < pair["thresh_sd"]:
        return {}

    pos_a = state.position.get(a, 0); pos_b = state.position.get(b, 0)

    out = {}
    if z > 0:
        # Sum too high → both legs likely overpriced. Sell both.
        room_a = max(0, LIMIT + pos_a)
        room_b = max(0, LIMIT + pos_b)
        if room_a > 0:
            orders, _ = _walk_book(da, -1, a, lambda px: px >= bb_a, room_a)
            if orders: out[a] = orders
        if room_b > 0:
            orders, _ = _walk_book(db, -1, b, lambda px: px >= bb_b, room_b)
            if orders: out[b] = orders
    else:
        room_a = max(0, LIMIT - pos_a)
        room_b = max(0, LIMIT - pos_b)
        if room_a > 0:
            orders, _ = _walk_book(da, +1, a, lambda px: px <= ba_a, room_a)
            if orders: out[a] = orders
        if room_b > 0:
            orders, _ = _walk_book(db, +1, b, lambda px: px <= ba_b, room_b)
            if orders: out[b] = orders
    return out


# ============================================================================
# Trader
# ============================================================================

class Trader:
    def bid(self):
        return 0

    def run(self, state: TradingState):
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}

        orders: dict[str, list[Order]] = {}

        # 1) Per-product z-take (all 50 products)
        for cfg in CFGS:
            ors = _z_take(state, cfg, td)
            if ors:
                orders[cfg["sym"]] = ors

        # v2: basket overlay disabled — see docstring
        trader_data = json.dumps(td)
        logger.flush(state, orders, 0, trader_data)
        return orders, 0, trader_data
