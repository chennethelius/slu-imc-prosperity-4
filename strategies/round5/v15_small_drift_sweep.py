"""
Round 5 v15 — sweep the remaining small-drift non-CFG products.

v14 IMC = 70,699. The 6 medium-drift additions paid:
  MICROCHIP_SQUARE         drift=-478  +\$5,036 (biggest)
  SLEEP_POD_LAMB_WOOL      drift=-590  +\$2,332
  TRANSLATOR_SPACE_GRAY    drift=-669  +\$2,264 (was -\$3.5k at target=0 in v6)
  OXYGEN_SHAKE_GARLIC      drift=+278  +\$1,102
  OXYGEN_SHAKE_MINT        drift=-243  +\$457
  OXYGEN_SHAKE_MORNING_BREATH drift=-218  ~\$0 (didn't fire much)

v15 sweeps the 4 remaining products with drift > 100 in v14 IMC log:

  PEBBLES_XS              drift=-190  target=-5
  TRANSLATOR_VOID_BLUE    drift=+175  target=+5
  SLEEP_POD_NYLON         drift=+168  target=+5
  MICROCHIP_CIRCLE        drift=-146  target=-5

Smaller drifts mean smaller inventory PnL (5*150=\$750 each) but spread
costs are also small. Expected per product: ~\$200-400. Combined: ~\$1k.

Original v14 docstring follows:

Round 5 v14 — 6 more trending non-CFG products (medium drift tier).

v13 IMC = 59,512. Pattern is firmly established: each non-CFG product
with directional D5 drift earns +\$1.3-2.5k when added with target_pos
matching the trend. Hit rate so far: 7/7 (3 from v12, 4 from v13).

v14 sweeps the next 6 products with drift > 200 in the v13 IMC log:

  TRANSLATOR_SPACE_GRAY        drift=-669  target=-5
  SLEEP_POD_LAMB_WOOL          drift=-590  target=-5
  MICROCHIP_SQUARE             drift=-478  target=-5
  OXYGEN_SHAKE_GARLIC          drift=+278  target=+5
  OXYGEN_SHAKE_MINT            drift=-243  target=-5
  OXYGEN_SHAKE_MORNING_BREATH  drift=-218  target=-5

SPACE_GRAY and LAMB_WOOL were tried in v6 with target=0 (lost \$3.5k
and \$2.1k from spread cost). With target=-5 matching the trend, the
inventory PnL (+\$3.3k and +\$2.95k respectively) should overpower the
spread cost. Net expected: ~breakeven on SPACE_GRAY, +\$0.8k on LAMB_WOOL.

Combined v14 expected: +\$3-5k. Target ~\$62-65k.
Worst case (a few products' trends reverse): ~\$57k vs v13 59.5k.

Original v13 docstring follows:

Round 5 v13 — 4 more trending non-CFG additions + PLANETARY_RINGS upgrade.

v12 IMC = 49,603. Three trend-matched non-CFG additions all paid:
  SLEEP_POD_COTTON  drift=+721 target=+5  → +\$4,334
  PEBBLES_S         drift=-559 target=-5  → +\$2,283
  MICROCHIP_OVAL    drift=-452 target=-5  → +\$2,082
Combined +\$8,699 from 3 additions (~\$2.9k average per product).

v13 adds 4 more strong-trend non-CFG products with the same recipe:

  UV_VISOR_MAGENTA   drift=-370 target=-5  est +\$1.3k
  PANEL_2X4          drift=-342 target=-5  est +\$1.2k
  ROBOT_MOPPING      drift=-339 target=-5  est +\$1.2k
  MICROCHIP_TRIANGLE drift=+276 target=+5  est +\$0.9k

Plus upgrade GALAXY_SOUNDS_PLANETARY_RINGS from Diversification tier
(size=3, min_half=3, target=-3) to Directional tier (size=4, min_half=3,
target=-5). Drift was -778 — the biggest magnitude in CFG — but capture
was negative (-\$158) because size=3 caps the inventory build. Bigger
size + bigger target should turn that into ~+\$2.5k.

Combined v13 expected: +\$5-7k. Target ~\$55-57k.

Original v12 docstring follows:

Round 5 v12 — add 3 non-CFG products with trend-matched target_pos.

v11 IMC = 40,904. The pattern is now clear: products with strong D5
trends generate \$2-3k each via inventory-direction capture when target_pos
matches the trend direction. v6 originally tried adding products with
target_pos=0 and lost \$2-4k each — pure MM on a trending wide-spread
product just bleeds spread cost.

Three non-CFG products with the strongest D5 drift are added here with
target_pos pre-set in the trend direction:

  SLEEP_POD_COTTON     drift=+721  target=+5  expected ~+\$1.5k
  PEBBLES_S            drift=-559  target=-5  expected ~+\$0.8k
  MICROCHIP_OVAL       drift=-452  target=-5  expected ~+\$0.5k

Conservative MM settings (size=4, min_half=3-4, inv_skew=15) similar to
existing directional-tier products. Expected combined: ~+\$2-4k.

Risk: trajectory reverses intra-day. All three were monotonic in the
v10 IMC log so reversal seems unlikely.

Original v11 docstring follows:

Round 5 v11 — set target_pos=+5 on the two pure-MM TRANSLATORs that
were trending up on D5.

v10 IMC = 38,005. ROBOT_LAUNDRY trend ride alone added +\$3,066 (vs
+\$179 with target=0). Two more pure-MM products had clear D5 trends:

  TRANSLATOR_ASTRO_BLACK     drift=+352, v10 pure-MM PnL=+1,468
  TRANSLATOR_ECLIPSE_CHARCOAL drift=+372, v10 pure-MM PnL=+1,172

Setting target=+5 captures the inventory direction on top of MM
spread. Linear scaling from ROBOT_LAUNDRY's +\$3k gain on -524 drift:
  ASTRO_BLACK   ~+\$2k  (352/524 of \$3k)
  ECLIPSE       ~+\$2.1k (372/524 of \$3k)

Expected combined gain: +\$4k. Target ~\$42k.

Original v10 docstring follows:

Round 5 v10 — surgical: remove flat-trajectory loser + ride observed trend.

v9 IMC = 34,669 (+\$5,777 over original mm.py 28,892). D5 trajectories
observed in the v9 IMC log give two more clean structural moves:

(1) SNACKPACK_RASPBERRY  — D5 mid trajectory: 10120 → 10121 (FLAT, ±1).
    Pure MM at target=0 still loses -\$270 to bid-ask spread cost on a
    flat-mid product (no mean-reversion, no trend, just pay the spread).
    REMOVE from CFG entirely — no quotes, no spread cost, no loss.

(2) ROBOT_LAUNDRY  — D5 mid trajectory: 9473 → 8949 (DROP 524 ticks).
    Pure MM at target=0 made +\$179. Setting target=-5 (short bias)
    captures the inventory direction: 5 lots short × 524-tick drop
    = ~\$2,620 inventory PnL from trend, on top of the +\$179 MM.
    Expected gain: ~\$2,000 (some erosion from lower MM volume on the
    long side as we lean short).

Combined v10 expectation: +\$2,270 over v9. Target ~\$37k.

Worst case (ROBOT_LAUNDRY trend reverses): -\$2k vs v9 = ~\$32.6k,
still > v8 baseline. Trajectory is monotonic (q1=9424, mid=9015,
q3=9057, end=8949), so reversal seems unlikely in current sim window.

Original v9 docstring follows:

Round 5 v9 — REVERSE directional bias on consistent trend-fade losers.

v8 set target_pos=0 (pure MM) on PANEL_1X2 and ROBOT_VACUUMING because
they were consistently losing. v8 IMC = 31,604 (+\$2,712 over mm.py).
But pure MM still left residual losses:
  PANEL_1X2:        -589 (down from -1,993)
  ROBOT_VACUUMING:  -332 (down from -1,640)

Both products had MONOTONIC trends on D5 from mm.py log:
  PANEL_1X2:       8849 → 8492 (drop 357 across 1000 ticks)
  ROBOT_VACUUMING: 8584 → 8811 (rise 227 across 1000 ticks)

If we REVERSE target_pos to ride the trend instead of fighting:
  PANEL_1X2 target=-5 (short bias, ride the drop)
  ROBOT_VACUUMING target=+5 (long bias, ride the rise)

Expected gain vs v8: +\$2-3k (mirror of the loss in mm.py original).
Expected total: ~\$33-34k.

Risk: if D5 has a reversal we haven't seen, we'd lose. But mid
trajectories observed were monotonic for both products.

Original v8 docstring follows:

Round 5 v8 — mm.py with directional bias REMOVED for consistent losers.

Across two IMC submissions of unmodified mm.py, two products lost on
BOTH runs (consistent, not single-trial noise):

                       mm.py #5    mm.py #6
  PANEL_1X2:           -2,603       -1,993       (target_pos was +5)
  ROBOT_VACUUMING:     -1,113       -1,640       (target_pos was -5)

Other products vary wildly between same-code runs (TRANSLATOR_GRAPHITE_MIST
+4,270 vs +2,845; UV_VISOR_RED 0 vs +3,258), so single-run results aren't
reliable. But these two products' loss DIRECTION was consistent across
both trials.

v8 fix: set target_pos=0 (pure MM) on both. Strategy still quotes them
and earns spread; it just stops trying to build a long position in
PANEL_1X2 (which keeps falling) or a short in ROBOT_VACUUMING (which
keeps rising).

Expected gain: ~$3-4k per submission IF the trend-fade losses were
genuine. Worst case loses the +X they'd have made on a reversal, but
2/2 consistent loss makes that scenario less likely.

Original mm.py docstring follows:

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


# Round 5 position limit per the brief: 10 for ALL 50 products.
POS_LIMIT = 10

# EWMA-based mean-reversion overlay. Each tick, deviation z = (mid - ewma)/sd.
# Adjust the target_pos by MR_K * (-z), so:
#   z > 0 (mid above ewma, "expensive")  → push target DOWN (favor selling)
#   z < 0 (mid below ewma, "cheap")      → push target UP   (favor buying)
# Capture is bounded by MR_CAP so the adjustment can't completely flip the
# directional bias (we still want to eat the per-day drift, just time entries
# and exits around local peaks/troughs).
EWMA_ALPHA = 0.93   # span ≈ 30 ticks. Fast enough to track trend, slow enough
                    # for local oscillations to create meaningful z.
MR_K = 1.5          # target shift per stddev of deviation
MR_CAP = 4          # max |mr_adj| in position units (don't flip base direction)
MR_MIN_VAR = 4.0    # require enough variance to compute z (avoid div-by-zero)

# Rolling mid-price window (ticks) used for local volatility + drift estimation.
# 50 ticks ≈ 5000 timestamp units — short enough to react to regime shifts,
# long enough to be a stable stddev estimate.
MID_WINDOW = 50

# Half-spread vol multiplier — but only ENGAGE when recent_vol exceeds the
# resting book half-spread, i.e., the local move scale is bigger than what
# the book is already pricing. Otherwise we'd widen on every tick of normal
# noise and forfeit fills on calm days.
VOL_K = 1.0
VOL_TRIGGER_MULT = 2.0  # only widen if recent_vol > VOL_TRIGGER_MULT * book_spread/4

# When |drift over MID_WINDOW| exceeds DRIFT_K * recent_stddev, treat as a
# sustained directional regime and shift fair AWAY. Random-walk null gives
# |drift| ≈ stddev × √N (≈ 7 for N=50), so DRIFT_K=4 only triggers on real
# directional moves, not normal noise accumulation.
DRIFT_K = 4.0

# Per-product MM config. min_half is the minimum half-spread we'll quote
# (in seashells). size is the quote depth per side. inv_skew is how many
# seashells we shift fair value per unit of inventory (full position →
# inv_skew shift). All values come from the round 5 spread/CV analysis.
CFG: dict[str, dict] = {
    # ----- MM-only (target_pos=0) — validated by real submission 564609 -----
    # v11: target_pos=+5 (was 0). D5 mid drift +372 ticks; ride the uptrend.
    "TRANSLATOR_ECLIPSE_CHARCOAL": {"size": 6, "min_half": 2, "inv_skew": 4, "target_pos": +5},
    # v11: target_pos=+5 (was 0). D5 mid drift +352 ticks; ride the uptrend.
    "TRANSLATOR_ASTRO_BLACK":      {"size": 6, "min_half": 2, "inv_skew": 4, "target_pos": +5},
    # v10: target_pos=-5 (was 0). D5 mid dropped 524 ticks; ride the trend.
    "ROBOT_LAUNDRY":               {"size": 6, "min_half": 2, "inv_skew": 4, "target_pos": -5},
    "SNACKPACK_PISTACHIO":         {"size": 6, "min_half": 4, "inv_skew": 6, "target_pos":  0},
    # v10: SNACKPACK_RASPBERRY removed entirely. D5 mid was flat (10120→10121),
    # so pure MM just paid the bid-ask spread cost (-\$270 on v9). No quotes
    # = no spread cost. (Original line: SNACKPACK_RASPBERRY size=6 target_pos=0)

    # ----- Directional bias (target_pos=±5) — half-limit so MM still works -----
    # Selected from per-product 1k-tick LOO sweep (3/3 wins, sorted by worst
    # held-out PnL so the floor is positive). target_pos is the SIGNED bias
    # we accumulate toward; inv_skew anchors fair around it.
    # v9: target_pos=-5 (REVERSE the losing direction — ride the downtrend).
    "PANEL_1X2":                   {"size": 4, "min_half": 2, "inv_skew": 15, "target_pos": -5},
    "UV_VISOR_AMBER":              {"size": 4, "min_half": 2, "inv_skew": 15, "target_pos": -5},
    "PEBBLES_M":                   {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": -5},
    "SLEEP_POD_SUEDE":             {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": +5},
    "MICROCHIP_RECTANGLE":         {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": -5},
    "GALAXY_SOUNDS_SOLAR_FLAMES":  {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": +5},
    "TRANSLATOR_GRAPHITE_MIST":    {"size": 4, "min_half": 2, "inv_skew": 15, "target_pos": +5},

    # ----- Spread legs (target=±5 each, paired) — captures within-group spread edge.
    # PANEL_4X4 - PANEL_1X2: PANEL_1X2 already at +5 directional; 4X4 paired at +5
    # PEBBLES_XL - PEBBLES_M: PEBBLES_M already at -5 directional; XL paired at +5
    # GALAXY_SOUNDS_SOLAR_FLAMES - DARK_MATTER: SF already at +5; DM paired at -5
    # PANEL_4X4 - PANEL_2X2: 2X2 paired at -5 (4X4 already at +5)
    # ROBOT_IRONING - ROBOT_VACUUMING: pair both at ±5
    "PANEL_4X4":                   {"size": 4, "min_half": 2, "inv_skew": 15, "target_pos": +5},
    "PEBBLES_XL":                  {"size": 4, "min_half": 4, "inv_skew": 15, "target_pos": +5},
    "GALAXY_SOUNDS_DARK_MATTER":   {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": -5},
    "PANEL_2X2":                   {"size": 4, "min_half": 2, "inv_skew": 15, "target_pos": -5},
    "ROBOT_IRONING":               {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": +5},
    # v9: target_pos=+5 (REVERSE the losing direction — ride the uptrend).
    "ROBOT_VACUUMING":             {"size": 4, "min_half": 2, "inv_skew": 15, "target_pos": +5},

    # ----- Diversification tier (target_pos=±3) — additional LOO-validated
    # spread legs that survived per-product flip stress test (>$2k flip cost).
    # YELLOW flipped to -3 (orig +3 was misdirected, flip improved by +$1,966
    # across all 3 days). OXYGEN_SHAKE pair + SLEEP_POD_POLYESTER dropped
    # (flip impact <$1.5k = signal indistinguishable from noise).
    "UV_VISOR_RED":                  {"size": 3, "min_half": 2, "inv_skew": 12, "target_pos": +3},
    "UV_VISOR_ORANGE":               {"size": 3, "min_half": 2, "inv_skew": 12, "target_pos": +3},
    "UV_VISOR_YELLOW":               {"size": 3, "min_half": 2, "inv_skew": 12, "target_pos": -3},
    # v13: upgrade size 3->4 + target -3->-5. Drift was -778 (largest in CFG)
    # but tier-2 sizing capped capture at -\$158. Tier-1 sizing should yield ~+\$2.5k.
    "GALAXY_SOUNDS_PLANETARY_RINGS": {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": -5},
    "PANEL_1X4":                     {"size": 3, "min_half": 2, "inv_skew": 12, "target_pos": -3},

    # ----- v12 ADDITIONS — non-CFG products with strong D5 trends. target_pos
    # set in the observed trend direction so inventory captures price move.
    # Conservative MM settings; risk-reward dominated by inventory PnL.
    "SLEEP_POD_COTTON":              {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": +5},
    "PEBBLES_S":                     {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": -5},
    "MICROCHIP_OVAL":                {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": -5},

    # ----- v13 ADDITIONS — 4 more trending non-CFG products -----
    "UV_VISOR_MAGENTA":              {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": -5},
    "PANEL_2X4":                     {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": -5},
    "ROBOT_MOPPING":                 {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": -5},
    "MICROCHIP_TRIANGLE":            {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": +5},

    # ----- v14 ADDITIONS — 6 more trending non-CFG products -----
    "TRANSLATOR_SPACE_GRAY":         {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": -5},
    "SLEEP_POD_LAMB_WOOL":           {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": -5},
    "MICROCHIP_SQUARE":              {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": -5},
    "OXYGEN_SHAKE_GARLIC":           {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": +5},
    "OXYGEN_SHAKE_MINT":             {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": -5},
    "OXYGEN_SHAKE_MORNING_BREATH":   {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": -5},

    # ----- v15 ADDITIONS — small-drift sweep -----
    "PEBBLES_XS":                    {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": -5},
    "TRANSLATOR_VOID_BLUE":          {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": +5},
    "SLEEP_POD_NYLON":               {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": +5},
    "MICROCHIP_CIRCLE":              {"size": 4, "min_half": 3, "inv_skew": 15, "target_pos": -5},
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

        # Persisted state: per-product EWMA of mid + EWMA of squared deviation
        # for online mean and variance estimates, plus cumulative cash flow
        # for the adaptive-scaling stop-loss.
        try:
            ts_state = json.loads(state.traderData) if state.traderData else {}
        except (json.JSONDecodeError, ValueError):
            ts_state = {}
        ewma_state: dict[str, dict] = ts_state.get("ewma", {})

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
            base_target = cfg.get("target_pos", 0)

            # Update EWMA of mid + EWMA of squared deviation (for online std).
            prev = ewma_state.get(sym)
            if prev is None:
                ewma_mid = fair
                ewma_var = 0.0
            else:
                ewma_mid = EWMA_ALPHA * prev["m"] + (1 - EWMA_ALPHA) * fair
                ewma_var = EWMA_ALPHA * prev["v"] + (1 - EWMA_ALPHA) * (fair - ewma_mid) ** 2
            ewma_state[sym] = {"m": ewma_mid, "v": ewma_var}

            # Mean-reversion target adjustment: lean target against deviation
            # from EWMA. Bounded by MR_CAP so we never flip the directional bias.
            if ewma_var > MR_MIN_VAR:
                z = (fair - ewma_mid) / math.sqrt(ewma_var)
                mr_adj = max(-MR_CAP, min(MR_CAP, -MR_K * z))
            else:
                mr_adj = 0.0

            target_pos = max(-POS_LIMIT, min(POS_LIMIT, base_target + mr_adj))

            # Inventory skew anchored at the dynamic target_pos.
            deviation = position - target_pos
            inv_shift = cfg["inv_skew"] * deviation / POS_LIMIT
            skewed_fair = fair - inv_shift

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

            # Take-the-cross: if the resting book is offering inside our fair,
            # eat it directly at the resting price. The engine fills any of
            # our limit orders priced through the book at the resting price
            # anyway, but explicit-take lets us size to actually-available
            # depth instead of quoting our full size and hoping for partial.
            ask_take_qty = 0
            if best_ask <= skewed_fair - half and buy_qty > 0:
                avail = abs(od.sell_orders[best_ask])
                ask_take_qty = min(avail, buy_qty)
                if ask_take_qty > 0:
                    ords.append(Order(sym, best_ask, ask_take_qty))

            bid_take_qty = 0
            if best_bid >= skewed_fair + half and sell_qty > 0:
                avail = od.buy_orders[best_bid]
                bid_take_qty = min(avail, sell_qty)
                if bid_take_qty > 0:
                    ords.append(Order(sym, best_bid, -bid_take_qty))

            # Quote the residual (after takes) — total committed buy/sell
            # qty must still respect capacity.
            quote_buy = max(0, buy_qty - ask_take_qty)
            quote_sell = max(0, sell_qty - bid_take_qty)

            if quote_buy > 0:
                ords.append(Order(sym, our_bid_px, quote_buy))
            if quote_sell > 0:
                ords.append(Order(sym, our_ask_px, -quote_sell))

            if ords:
                orders[sym] = ords

        new_trader_data = json.dumps({"ewma": ewma_state}, separators=(",", ":"))
        logger.flush(state, orders, 0, new_trader_data)
        return orders, 0, new_trader_data


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