"""
disc_time_phase: Time-conditioned aggressiveness schedule.

Per sbmxz1143's observation: "for pepper the order book starts with asks heavy
then it becomes variable in between there are some only bid 0 ask cases also
and at the end it stabilizes"

Phases (using within-day tick count, 0-999900):
  EARLY  [0,      200000): ask-heavy → accumulate aggressively toward +80
  MID    [200000, 800000): variable → MM around trend-aware fair
  LATE   [800000, 999900]: stable → exit or MM neutral

OSMIUM: steady MM throughout (mean-reverting around 10000).

Anti-overfit: phase boundaries chosen by structural reasoning, not grid search.
No per-phase parameter tuning — same fair value logic in all phases, just
different aggressiveness caps.
"""
from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    # Phase boundaries as fraction of day
    PEP_TARGET = 80
    PEP_EARLY_DEPTH = 8
    PEP_MID_DEPTH = 3
    PEP_LATE_DEPTH = 0

    OSM_FAIR = 10000
    OSM_EWMA_ALPHA = 0.05
    OSM_TAKE = 2
    OSM_EDGE = 2

    def bid(self):
        return 15

    def run(self, state: TradingState):
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}

        result: dict[str, list[Order]] = {}
        within_day = state.timestamp % 1_000_000
        for symbol, depth in state.order_depths.items():
            pos = state.position.get(symbol, 0)
            if symbol == "ASH_COATED_OSMIUM":
                result[symbol] = self._osmium(depth, pos, td)
            elif symbol == "INTARIAN_PEPPER_ROOT":
                result[symbol] = self._pepper(symbol, depth, pos, within_day, td)
            else:
                result[symbol] = []
        return result, 0, json.dumps(td)

    def _pepper(self, symbol, d, pos, within_day, td):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders); ba = min(d.sell_orders)
        if bb >= ba:
            return []
        mid = (bb + ba) / 2.0

        # EWMA fair for pepper
        fair = td.get("pep_fair", mid)
        fair = 0.98 * fair + 0.02 * mid
        td["pep_fair"] = fair

        if within_day < 200_000:
            phase = "early"
            depth_tol = self.PEP_EARLY_DEPTH
            target = self.PEP_TARGET
        elif within_day < 800_000:
            phase = "mid"
            depth_tol = self.PEP_MID_DEPTH
            target = self.PEP_TARGET
        else:
            phase = "late"
            depth_tol = self.PEP_LATE_DEPTH
            # In late phase, keep target (pepper still drifts up; no reason to exit)
            target = self.PEP_TARGET

        lim = self.LIMITS[symbol]
        orders = []
        need = target - pos

        if need > 0:
            to_buy = min(need, lim - pos)
            for a in sorted(d.sell_orders):
                if a > mid + depth_tol:
                    break
                vol = min(-d.sell_orders[a], to_buy)
                if vol > 0:
                    orders.append(Order(symbol, a, vol))
                    to_buy -= vol
                if to_buy <= 0:
                    break
            if to_buy > 0 and phase != "late":
                orders.append(Order(symbol, bb + 1, to_buy))

        # Keep +80 position — pepper drifts up; no reason to exit
        return orders

    def _osmium(self, d, pos, td):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders); ba = min(d.sell_orders)
        if bb >= ba:
            return []
        mid = (bb + ba) / 2.0

        fair = td.get("osm_fair", self.OSM_FAIR)
        fair = (1 - self.OSM_EWMA_ALPHA) * fair + self.OSM_EWMA_ALPHA * mid
        td["osm_fair"] = fair

        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        orders = []
        bv = sv = 0

        if ba <= fair - self.OSM_TAKE:
            q = min(-d.sell_orders[ba], lim - pos - bv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", ba, q))
                bv += q
        if bb >= fair + self.OSM_TAKE:
            q = min(d.buy_orders[bb], lim + pos - sv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", bb, -q))
                sv += q

        bid_p = int(fair - self.OSM_EDGE)
        ask_p = int(fair + self.OSM_EDGE)
        buy_q = max(0, lim - pos - bv)
        sell_q = max(0, lim + pos - sv)
        if buy_q > 0:
            orders.append(Order("ASH_COATED_OSMIUM", bid_p, buy_q))
        if sell_q > 0:
            orders.append(Order("ASH_COATED_OSMIUM", ask_p, -sell_q))
        return orders
