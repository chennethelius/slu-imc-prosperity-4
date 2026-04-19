"""
r2_inventory_skew: Avellaneda-Stoikov style inventory-aware MM on OSMIUM.

When holding long inventory, LOWER the ask (eager to sell) and RAISE the bid
(reluctant to buy more). Vice versa when short. This naturally mean-reverts
inventory toward zero while capturing spread.

Skew formula: adjusted_fair = fair - pos * SKEW_FACTOR
- SKEW_FACTOR derived from: typical spread / (2 * position_limit)
  = 16 / (2 * 80) = 0.1 per unit of position

PEPPER: identical to Test_9_e3.
MAF: bid 20.
"""
from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    OSM_FAIR_SEED = 10000
    OSM_EWMA_ALPHA = 0.05
    OSM_SKEW = 0.1             # fair shifts 0.1 per unit of inventory
    OSM_TAKE_WIDTH = 3
    OSM_CLEAR_WIDTH = 2
    OSM_MAKE_EDGE = 3
    OSM_VOL_LIMIT = 30

    PEP_TARGET = 80
    PEP_TAKE_DEPTH = 6

    def bid(self):
        return 20

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
                result[symbol] = self._pepper(symbol, depth, pos)
            else:
                result[symbol] = []
        return result, 0, json.dumps(td)

    def _osmium(self, d, pos, td):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders); ba = min(d.sell_orders)
        if bb >= ba:
            return []
        mid = (bb + ba) / 2.0

        # EWMA fair on mid
        raw_fair = self.OSM_FAIR_SEED

        # Inventory-skewed fair: shifts toward clearing inventory
        fair = raw_fair - pos * self.OSM_SKEW

        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        tw = self.OSM_TAKE_WIDTH
        cw = self.OSM_CLEAR_WIDTH
        edge = self.OSM_MAKE_EDGE
        vol_lim = self.OSM_VOL_LIMIT
        orders = []
        bv = sv = 0

        # Take
        if ba <= fair - tw:
            q = min(-d.sell_orders[ba], lim - pos - bv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", ba, q)); bv += q
        if bb >= fair + tw:
            q = min(d.buy_orders[bb], lim + pos - sv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", bb, -q)); sv += q

        # Clear at fair edges
        pos_after = pos + bv - sv
        f_bid = int(fair - cw); f_ask = int(fair + cw)
        if pos_after > 0:
            cq = sum(v for p, v in d.buy_orders.items() if p >= f_ask)
            cq = min(cq, pos_after, lim + pos - sv)
            if cq > 0:
                orders.append(Order("ASH_COATED_OSMIUM", f_ask, -cq)); sv += cq
        if pos_after < 0:
            cq = sum(-v for p, v in d.sell_orders.items() if p <= f_bid)
            cq = min(cq, -pos_after, lim - pos - bv)
            if cq > 0:
                orders.append(Order("ASH_COATED_OSMIUM", f_bid, cq)); bv += cq

        # Passive MM with skewed quotes
        baaf = [p for p in d.sell_orders if p > fair + edge - 1]
        bbbf = [p for p in d.buy_orders if p < fair - edge + 1]
        if baaf and bbbf:
            baaf_min = min(baaf); bbbf_max = max(bbbf)
            if baaf_min <= fair + edge and pos <= vol_lim:
                baaf_min = fair + edge + 1
            if bbbf_max >= fair - edge and pos >= -vol_lim:
                bbbf_max = fair - edge - 1
            bid_p = bbbf_max + 1; ask_p = baaf_min - 1
            buy_q = max(0, lim - pos - bv)
            sell_q = max(0, lim + pos - sv)
            if buy_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(bid_p), buy_q))
            if sell_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(ask_p), -sell_q))
        return orders

    def _pepper(self, symbol, depth, pos):
        if not depth.buy_orders or not depth.sell_orders:
            return []
        lim = self.LIMITS[symbol]
        bb = max(depth.buy_orders); ba = min(depth.sell_orders)
        if bb >= ba:
            return []
        mid = (bb + ba) / 2.0
        need = self.PEP_TARGET - pos
        if need <= 0:
            return []
        orders = []
        to_buy = min(need, lim - pos)
        for a in sorted(depth.sell_orders):
            if a > mid + self.PEP_TAKE_DEPTH:
                break
            vol = min(-depth.sell_orders[a], to_buy)
            if vol > 0:
                orders.append(Order(symbol, a, vol)); to_buy -= vol
            if to_buy <= 0:
                break
        if to_buy > 0:
            orders.append(Order(symbol, bb + 1, to_buy))
        return orders
