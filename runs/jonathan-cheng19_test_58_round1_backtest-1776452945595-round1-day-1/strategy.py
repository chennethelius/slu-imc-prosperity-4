from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    """
    Test_58 — pepper mid = volume-weighted micro-price.

    vs Test_55: replaces the naive pepper mid (bb+ba)/2 with the same
    volume-weighted micro-price that osmium uses:
        micro = (bb·av + ba·bv) / (bv + av)
    This gives a lower-variance fair estimator — mid leans toward the
    deeper side, which reduces noise in the running-mean intercept and
    produces sharper entry decisions (PEP_ENTRY_TAKE threshold is
    applied to a tighter fair).

    Everything else unchanged from Test_55. Same 13 params.
    """

    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    OSM_K_SS = 0.1353
    OSM_FAIR_STATIC = 10000
    OSM_TAKE_WIDTH = 3
    OSM_CLEAR_WIDTH = 2
    OSM_CLEAR_TIGHT_POS = 50
    OSM_VOLUME_LIMIT = 30
    OSM_MAKE_EDGE = 2
    OSM_SKEW_UNIT = 24

    PEP_DRIFT = 0.100188
    PEP_ENTRY_TAKE = 7
    PEP_ENTRY_TIMEOUT = 200
    PEP_BID_FLOOR = -6
    PEP_BID_CEIL = 5

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
                result[symbol] = self._osmium(depth, pos, td)
            elif symbol == "INTARIAN_PEPPER_ROOT":
                result[symbol] = self._pepper(symbol, depth, pos, state.timestamp, td)
            else:
                result[symbol] = []
        return result, 0, json.dumps(td)

    def _kalman_fair(self, depth, td):
        if not depth.buy_orders or not depth.sell_orders:
            return td.get("_osm_f", self.OSM_FAIR_STATIC)
        bb = max(depth.buy_orders)
        ba = min(depth.sell_orders)
        bv = depth.buy_orders[bb]
        av = -depth.sell_orders[ba]
        tot = bv + av
        micro = (bb * av + ba * bv) / tot if tot > 0 else (bb + ba) / 2.0
        f = td.get("_osm_f", micro)
        f += self.OSM_K_SS * (micro - f)
        td["_osm_f"] = f
        return f

    def _osmium(self, d, pos, td):
        if not d.buy_orders or not d.sell_orders:
            return []
        fair = self._kalman_fair(d, td)
        take_buy = max(self.OSM_FAIR_STATIC, fair)
        take_sell = min(self.OSM_FAIR_STATIC, fair)

        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        cw = self.OSM_CLEAR_WIDTH - (abs(pos) >= self.OSM_CLEAR_TIGHT_POS)
        orders = []
        bv = sv = 0

        skew = round(pos / self.OSM_SKEW_UNIT)
        tw_ask = max(0, self.OSM_TAKE_WIDTH + skew)
        tw_bid = max(0, self.OSM_TAKE_WIDTH - skew)

        ba = min(d.sell_orders)
        if ba <= take_buy - tw_ask:
            q = min(-d.sell_orders[ba], lim - pos - bv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", ba, q)); bv += q
        bb = max(d.buy_orders)
        if bb >= take_sell + tw_bid:
            q = min(d.buy_orders[bb], lim + pos - sv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", bb, -q)); sv += q

        pos_after = pos + bv - sv
        f_bid = int(round(fair - cw))
        f_ask = int(round(fair + cw))
        if pos_after > 0:
            cq = min(pos_after, sum(v for p, v in d.buy_orders.items() if p >= f_ask))
            sent = min(lim + pos - sv, cq)
            if sent > 0:
                orders.append(Order("ASH_COATED_OSMIUM", f_ask, -sent)); sv += sent
        elif pos_after < 0:
            cq = min(-pos_after, sum(-v for p, v in d.sell_orders.items() if p <= f_bid))
            sent = min(lim - pos - bv, cq)
            if sent > 0:
                orders.append(Order("ASH_COATED_OSMIUM", f_bid, sent)); bv += sent

        bid_edge = max(1, self.OSM_MAKE_EDGE + skew)
        ask_edge = max(1, self.OSM_MAKE_EDGE - skew)
        ask_gate = fair + ask_edge - 1
        bid_gate = fair - bid_edge + 1
        baaf = min((p for p in d.sell_orders if p > ask_gate), default=None)
        bbbf = max((p for p in d.buy_orders if p < bid_gate), default=None)
        if baaf is not None and bbbf is not None:
            if baaf <= fair + ask_edge and pos <= self.OSM_VOLUME_LIMIT:
                baaf = int(round(fair + ask_edge + 1))
            if bbbf >= fair - bid_edge and pos >= -self.OSM_VOLUME_LIMIT:
                bbbf = int(round(fair - bid_edge - 1))
            buy_q = lim - pos - bv
            if buy_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(bbbf + 1), buy_q))
            sell_q = lim + pos - sv
            if sell_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(baaf - 1), -sell_q))
        return orders

    def _pepper(self, symbol, depth, pos, timestamp, td):
        if not depth.buy_orders or not depth.sell_orders:
            return []
        lim = self.LIMITS["INTARIAN_PEPPER_ROOT"]
        tick = timestamp // 100

        bb = max(depth.buy_orders)
        ba = min(depth.sell_orders)
        bv_tob = depth.buy_orders[bb]
        av_tob = -depth.sell_orders[ba]
        tot = bv_tob + av_tob
        mid = (bb * av_tob + ba * bv_tob) / tot if tot > 0 else (bb + ba) / 2.0

        pep_sum = td.get("_pep_sum", 0.0) + mid - self.PEP_DRIFT * tick
        pep_cnt = td.get("_pep_cnt", 0) + 1
        td["_pep_sum"] = pep_sum
        td["_pep_cnt"] = pep_cnt

        fair = pep_sum / pep_cnt + self.PEP_DRIFT * tick
        fair_int = int(round(fair))

        need = lim - pos
        if need <= 0:
            return []

        orders = []
        bv = 0
        selective = tick < self.PEP_ENTRY_TIMEOUT
        threshold = fair + self.PEP_ENTRY_TAKE if selective else float("inf")

        for a in sorted(depth.sell_orders):
            if bv >= need or a > threshold:
                break
            vol = min(-depth.sell_orders[a], need - bv)
            if vol > 0:
                orders.append(Order(symbol, a, vol))
                bv += vol

        if selective and bv < need:
            competing = max(depth.buy_orders)
            offset = max(self.PEP_BID_FLOOR, min(self.PEP_BID_CEIL, competing + 1 - fair_int))
            orders.append(Order(symbol, fair_int + offset, need - bv))

        return orders
