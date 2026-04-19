"""
disc_spread_regime: Spread-width regime filter.

Per henryendix: "trying to use some spread signal factors for regime change"
(claimed >8.3k pepper).

Narrow spread → trending/low-volatility regime → aggressive taking
Wide spread → uncertain/toxic regime → pull orders, reduce size

Applied to both products:
- PEPPER: aggressive toward target when spread <= NARROW_SPREAD; halt
  taking when spread >= WIDE_SPREAD
- OSMIUM: scale take width by spread regime (tighter in calm, wider in vol)

Parameters chosen from data structure (spread distribution, not tuned):
- NARROW = 25th percentile of observed spreads
- WIDE = 75th percentile
- Tracked via EWMA of observed spreads
"""
from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    PEP_TARGET = 80
    PEP_SPREAD_ALPHA = 0.03

    OSM_FAIR_SEED = 10000
    OSM_EWMA_ALPHA = 0.05
    OSM_BASE_TAKE = 2
    OSM_BASE_EDGE = 2

    def bid(self):
        return 15

    def run(self, state: TradingState):
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}

        result: dict[str, list[Order]] = {}
        for symbol, depth in state.order_depths.items():
            pos = state.position.get(symbol, 0)
            if symbol == "ASH_COATED_OSMIUM":
                result[symbol] = self._osmium(depth, pos, td)
            elif symbol == "INTARIAN_PEPPER_ROOT":
                result[symbol] = self._pepper(symbol, depth, pos, td)
            else:
                result[symbol] = []
        return result, 0, json.dumps(td)

    @staticmethod
    def _regime(spread_mean, spread):
        """Returns 'narrow', 'mid', 'wide' based on current spread vs EWMA."""
        if spread <= spread_mean * 0.7:
            return "narrow"
        if spread >= spread_mean * 1.3:
            return "wide"
        return "mid"

    def _pepper(self, symbol, d, pos, td):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders); ba = min(d.sell_orders)
        if bb >= ba:
            return []
        spread = ba - bb
        mid = (bb + ba) / 2.0

        sp_mean = td.get("pep_spread", spread)
        sp_mean = (1 - self.PEP_SPREAD_ALPHA) * sp_mean + self.PEP_SPREAD_ALPHA * spread
        td["pep_spread"] = sp_mean
        regime = self._regime(sp_mean, spread)

        lim = self.LIMITS[symbol]
        orders = []
        need = self.PEP_TARGET - pos
        if need <= 0:
            return []

        # Aggressive in narrow regime (trending), cautious in wide (uncertain)
        if regime == "narrow":
            take_depth = 8  # willing to pay up for immediacy
        elif regime == "mid":
            take_depth = 4
        else:  # wide
            take_depth = 0  # only take inside-spread, no chasing

        to_buy = min(need, lim - pos)
        for a in sorted(d.sell_orders):
            if a > mid + take_depth:
                break
            vol = min(-d.sell_orders[a], to_buy)
            if vol > 0:
                orders.append(Order(symbol, a, vol))
                to_buy -= vol
            if to_buy <= 0:
                break
        # Passive bid as fallback
        if to_buy > 0 and regime != "wide":
            orders.append(Order(symbol, bb + 1, to_buy))
        return orders

    def _osmium(self, d, pos, td):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders); ba = min(d.sell_orders)
        if bb >= ba:
            return []
        spread = ba - bb
        mid = (bb + ba) / 2.0

        # EWMA fair
        fair = td.get("osm_fair", self.OSM_FAIR_SEED)
        fair = (1 - self.OSM_EWMA_ALPHA) * fair + self.OSM_EWMA_ALPHA * mid
        td["osm_fair"] = fair

        # Spread regime
        sp_mean = td.get("osm_spread", spread)
        sp_mean = (1 - self.OSM_EWMA_ALPHA) * sp_mean + self.OSM_EWMA_ALPHA * spread
        td["osm_spread"] = sp_mean
        regime = self._regime(sp_mean, spread)

        if regime == "narrow":
            take_w, edge = self.OSM_BASE_TAKE - 1, self.OSM_BASE_EDGE - 1
        elif regime == "wide":
            take_w, edge = self.OSM_BASE_TAKE + 2, self.OSM_BASE_EDGE + 1
        else:
            take_w, edge = self.OSM_BASE_TAKE, self.OSM_BASE_EDGE
        take_w = max(1, take_w)
        edge = max(1, edge)

        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        orders = []
        bv = sv = 0

        if ba <= fair - take_w:
            q = min(-d.sell_orders[ba], lim - pos - bv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", ba, q))
                bv += q
        if bb >= fair + take_w:
            q = min(d.buy_orders[bb], lim + pos - sv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", bb, -q))
                sv += q

        bid_p = int(fair - edge)
        ask_p = int(fair + edge)
        buy_q = max(0, lim - pos - bv)
        sell_q = max(0, lim + pos - sv)
        if buy_q > 0 and regime != "wide":
            orders.append(Order("ASH_COATED_OSMIUM", bid_p, buy_q))
        if sell_q > 0 and regime != "wide":
            orders.append(Order("ASH_COATED_OSMIUM", ask_p, -sell_q))
        return orders
