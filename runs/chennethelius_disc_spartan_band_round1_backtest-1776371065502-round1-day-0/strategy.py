"""
disc_spartan_band: Wide no-trade band to avoid overtrading.

Per spartan_35961: "Pepper is currently negative edge... overtrading it and
losing ~25k. Widen thresholds, add no-trade band around fair value, reduce
trading frequency. Osmium I'll leave unchanged since it's the only consistent
positive."

PEPPER: Hold +80 target always (capture drift), but ONLY re-trade to exit
inventory when price deviates >BAND from EWMA fair. No active MM.

OSMIUM: Conservative MM around 10000 (less edge than Test_9_e3 to reduce
overfitting risk).

Parameters derived from data properties, not tuned:
- BAND = 2x typical tick volatility (≈ round-trip cost)
- EWMA alpha from drift timescale (~100 ticks)
"""
from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    PEP_TARGET = 80
    PEP_BAND = 4               # no-trade band width (price units from fair)
    PEP_EWMA_ALPHA = 0.02      # ~50-tick half-life
    PEP_SEED = 12000

    OSM_FAIR = 10000
    OSM_TAKE = 2
    OSM_CLEAR = 1
    OSM_EDGE = 2

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
                result[symbol] = self._osmium(depth, pos)
            elif symbol == "INTARIAN_PEPPER_ROOT":
                result[symbol] = self._pepper(symbol, depth, pos, td)
            else:
                result[symbol] = []
        return result, 0, json.dumps(td)

    def _pepper(self, symbol, d, pos, td):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders)
        ba = min(d.sell_orders)
        if bb >= ba:
            return []
        mid = (bb + ba) / 2.0

        fair = td.get("pep_fair", self.PEP_SEED)
        fair = (1 - self.PEP_EWMA_ALPHA) * fair + self.PEP_EWMA_ALPHA * mid
        td["pep_fair"] = fair

        lim = self.LIMITS[symbol]
        target = self.PEP_TARGET
        band = self.PEP_BAND
        orders = []

        # Accumulate toward target aggressively if under-positioned
        need = target - pos
        if need > 0 and ba <= fair + band:
            # Buy if ask within acceptable zone
            to_buy = min(need, lim - pos)
            for a in sorted(d.sell_orders):
                if a > fair + band:
                    break
                vol = min(-d.sell_orders[a], to_buy)
                if vol > 0:
                    orders.append(Order(symbol, a, vol))
                    to_buy -= vol
                if to_buy <= 0:
                    break

        # Exit over-long only if price is RICH (above fair + band) — capture profit
        if pos > target and bb >= fair + band:
            to_sell = min(pos - target, lim + pos)
            for b in sorted(d.buy_orders, reverse=True):
                if b < fair + band:
                    break
                vol = min(d.buy_orders[b], to_sell)
                if vol > 0:
                    orders.append(Order(symbol, b, -vol))
                    to_sell -= vol
                if to_sell <= 0:
                    break

        return orders

    def _osmium(self, d, pos):
        if not d.buy_orders or not d.sell_orders:
            return []
        fair = self.OSM_FAIR
        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        orders = []
        bv = sv = 0

        ba = min(d.sell_orders)
        if ba <= fair - self.OSM_TAKE:
            q = min(-d.sell_orders[ba], lim - pos - bv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", ba, q))
                bv += q
        bb = max(d.buy_orders)
        if bb >= fair + self.OSM_TAKE:
            q = min(d.buy_orders[bb], lim + pos - sv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", bb, -q))
                sv += q

        # Passive MM around fair
        bid_p = fair - self.OSM_EDGE
        ask_p = fair + self.OSM_EDGE
        buy_q = max(0, lim - pos - bv)
        sell_q = max(0, lim + pos - sv)
        if buy_q > 0:
            orders.append(Order("ASH_COATED_OSMIUM", bid_p, buy_q))
        if sell_q > 0:
            orders.append(Order("ASH_COATED_OSMIUM", ask_p, -sell_q))
        return orders
