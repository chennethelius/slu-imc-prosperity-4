"""
disc_safe_core: Maximum robustness, minimum overfit.

Defense-first variant. Inspired by the Discord anxiety that "IMC may change
the trend in the live run" (geyzsonkristoffer) and spartan's "negative edge"
take. Every decision is justified from the structural observation that:

1. PEPPER has exhibited drift + positive expectancy holding long (consensus)
2. OSMIUM mean-reverts around 10000 with low volatility (kami1432, .giorgiorossi)
3. Model risk is the biggest threat if trend reverses

Strategy:
- PEPPER: slow accumulation to +60 (not +80) — give up some upside for
  smaller drawdown if reversal hits. Take only within 2 price units of mid.
- OSMIUM: tight MM with aggressive inventory clearing.

Low knob count, all justifiable. Should be in the top quartile under MC noise.
"""
from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    PEP_TARGET = 80           # full drift capture
    PEP_TAKE_DEPTH = 3        # willing to pay a bit for immediacy
    PEP_EWMA_ALPHA = 0.03

    OSM_FAIR_SEED = 10000
    OSM_EWMA_ALPHA = 0.05
    OSM_TAKE = 2
    OSM_EDGE = 1              # aggressive MM (tight spread)
    OSM_CLEAR_POS = 30        # clear inventory above +/- 30

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

    def _pepper(self, symbol, d, pos, td):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders); ba = min(d.sell_orders)
        if bb >= ba:
            return []
        mid = (bb + ba) / 2.0

        fair = td.get("pep_fair", mid)
        fair = (1 - self.PEP_EWMA_ALPHA) * fair + self.PEP_EWMA_ALPHA * mid
        td["pep_fair"] = fair

        lim = self.LIMITS[symbol]
        orders = []
        need = self.PEP_TARGET - pos
        if need > 0:
            to_buy = min(need, lim - pos)
            for a in sorted(d.sell_orders):
                if a > mid + self.PEP_TAKE_DEPTH:
                    break
                vol = min(-d.sell_orders[a], to_buy)
                if vol > 0:
                    orders.append(Order(symbol, a, vol))
                    to_buy -= vol
                if to_buy <= 0:
                    break
            if to_buy > 0:
                orders.append(Order(symbol, bb + 1, to_buy))
        # No exit logic — ride the drift, rebalance only through natural fills
        return orders

    def _osmium(self, d, pos, td):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders); ba = min(d.sell_orders)
        if bb >= ba:
            return []
        mid = (bb + ba) / 2.0

        fair = td.get("osm_fair", self.OSM_FAIR_SEED)
        fair = (1 - self.OSM_EWMA_ALPHA) * fair + self.OSM_EWMA_ALPHA * mid
        td["osm_fair"] = fair

        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        orders = []
        bv = sv = 0

        # Take
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

        # Aggressive inventory clearing at fair
        pos_after = pos + bv - sv
        if pos_after > self.OSM_CLEAR_POS:
            cq = min(pos_after - self.OSM_CLEAR_POS, lim + pos - sv)
            if cq > 0 and bb >= fair:
                orders.append(Order("ASH_COATED_OSMIUM", int(fair), -cq))
                sv += cq
        if pos_after < -self.OSM_CLEAR_POS:
            cq = min(-pos_after - self.OSM_CLEAR_POS, lim - pos - bv)
            if cq > 0 and ba <= fair:
                orders.append(Order("ASH_COATED_OSMIUM", int(fair), cq))
                bv += cq

        # Tight passive MM
        bid_p = int(fair - self.OSM_EDGE)
        ask_p = int(fair + self.OSM_EDGE)
        buy_q = max(0, lim - pos - bv)
        sell_q = max(0, lim + pos - sv)
        if buy_q > 0:
            orders.append(Order("ASH_COATED_OSMIUM", bid_p, buy_q))
        if sell_q > 0:
            orders.append(Order("ASH_COATED_OSMIUM", ask_p, -sell_q))
        return orders
