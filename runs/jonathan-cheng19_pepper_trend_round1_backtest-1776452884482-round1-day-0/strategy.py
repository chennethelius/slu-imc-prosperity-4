from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List
import json


class Trader:
    """
    pepper_trend: pure directional pepper — detect drift, load toward target,
    hold. No MM on pepper. OSM unchanged from pepper_hybrid.
    """

    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    OSM_FAIR = 10000
    OSM_TAKE_WIDTH = 1
    OSM_CLEAR_WIDTH = 0
    OSM_VOLUME_LIMIT = 20

    PEP_WINDOW = 50
    PEP_SLOPE_TRIGGER = 0.005
    PEP_TARGET = 70

    def bid(self):
        return 15

    def run(self, state: TradingState):
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}

        result: dict[str, list[Order]] = {}
        for symbol, depth in state.order_depths.items():
            if symbol not in self.LIMITS:
                result[symbol] = []
                continue
            pos = state.position.get(symbol, 0)
            if symbol == "ASH_COATED_OSMIUM":
                result[symbol] = self._osmium(depth, pos)
            elif symbol == "INTARIAN_PEPPER_ROOT":
                result[symbol] = self._pepper(symbol, depth, pos, td)
            else:
                result[symbol] = []
        return result, 0, json.dumps(td)

    def _osmium(self, d, pos):
        if not d.buy_orders or not d.sell_orders:
            return []
        fair = self.OSM_FAIR
        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        tw = self.OSM_TAKE_WIDTH
        cw = self.OSM_CLEAR_WIDTH
        vol_lim = self.OSM_VOLUME_LIMIT
        orders = []
        bv = sv = 0

        ba = min(d.sell_orders); ba_amt = -d.sell_orders[ba]
        if ba <= fair - tw:
            q = min(ba_amt, lim - pos - bv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", ba, q)); bv += q
        bb = max(d.buy_orders); bb_amt = d.buy_orders[bb]
        if bb >= fair + tw:
            q = min(bb_amt, lim + pos - sv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", bb, -q)); sv += q

        pos_after = pos + bv - sv
        f_bid = fair - cw
        f_ask = fair + cw
        if pos_after > 0:
            cq = sum(v for p, v in d.buy_orders.items() if p >= f_ask)
            cq = min(cq, pos_after)
            sent = min(lim + pos - sv, cq)
            if sent > 0:
                orders.append(Order("ASH_COATED_OSMIUM", f_ask, -sent)); sv += sent
        if pos_after < 0:
            cq = sum(-v for p, v in d.sell_orders.items() if p <= f_bid)
            cq = min(cq, -pos_after)
            sent = min(lim - pos - bv, cq)
            if sent > 0:
                orders.append(Order("ASH_COATED_OSMIUM", f_bid, sent)); bv += sent

        baaf = [p for p in d.sell_orders if p > fair + 1]
        bbbf = [p for p in d.buy_orders if p < fair - 1]
        if baaf and bbbf:
            baaf = min(baaf); bbbf = max(bbbf)
            if baaf <= fair + 2 and pos <= vol_lim:
                baaf = fair + 3
            if bbbf >= fair - 2 and pos >= -vol_lim:
                bbbf = fair - 3
            bid = bbbf + 1; ask = baaf - 1
            buy_q = lim - pos - bv
            if buy_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(bid), buy_q))
            sell_q = lim + pos - sv
            if sell_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(ask), -sell_q))
        return orders

    def _pepper(self, symbol, depth, pos, td):
        if not depth.buy_orders or not depth.sell_orders:
            return []

        bid_wall = min(depth.buy_orders.keys())
        ask_wall = max(depth.sell_orders.keys())
        fv = (bid_wall + ask_wall) / 2.0

        hist = td.get("pep_hist", [])
        hist.append(fv)
        if len(hist) > self.PEP_WINDOW:
            hist = hist[-self.PEP_WINDOW:]
        td["pep_hist"] = hist

        if len(hist) < self.PEP_WINDOW:
            return []

        slope = (hist[-1] - hist[0]) / len(hist)
        if slope > self.PEP_SLOPE_TRIGGER:
            target = self.PEP_TARGET
        elif slope < -self.PEP_SLOPE_TRIGGER:
            target = -self.PEP_TARGET
        else:
            target = 0

        lim = self.LIMITS["INTARIAN_PEPPER_ROOT"]
        orders = []
        asks = sorted(depth.sell_orders.keys())
        bids = sorted(depth.buy_orders.keys(), reverse=True)
        need = target - pos

        if need > 0:
            to_buy = min(need, lim - pos)
            for a in asks:
                if to_buy <= 0:
                    break
                vol = min(-depth.sell_orders[a], to_buy)
                if vol > 0:
                    orders.append(Order(symbol, a, vol))
                    to_buy -= vol
            if to_buy > 0:
                bp = bids[0] + 1
                orders.append(Order(symbol, bp, to_buy))
        elif need < 0:
            to_sell = min(-need, lim + pos)
            for b in bids:
                if to_sell <= 0:
                    break
                vol = min(depth.buy_orders[b], to_sell)
                if vol > 0:
                    orders.append(Order(symbol, b, -vol))
                    to_sell -= vol
            if to_sell > 0:
                ap = asks[0] - 1
                orders.append(Order(symbol, ap, -to_sell))

        return orders
