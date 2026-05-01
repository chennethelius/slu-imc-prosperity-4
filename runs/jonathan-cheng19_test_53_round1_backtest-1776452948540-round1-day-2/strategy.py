from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    """
    Test_53 — simplified candidate. Same strategy as Test_40/best.py
    with 5 fewer params and shorter Kalman.

    Changes vs candidate.py:
      * Steady-state Kalman gain replaces var tracking.
        Solving p² - Q·p - Q·R = 0 with Q=0.141, R=6.656 gives p=1.042,
        K_SS = p/(p+R) = 0.1353. Converges in ~10 ticks anyway; fixed K
        is indistinguishable in steady state and drops one param plus
        the var bookkeeping.
      * Inline OSM_MIN_TW=0 and OSM_MIN_EDGE=1 (were constants).
      * Derive PEP_TARGET from the position limit.

    19 → 14 params. Behavior target: identical to candidate within
    rounding/warmup noise.
    """

    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    OSM_K_SS = 0.1353
    OSM_FAIR_STATIC = 10000
    OSM_TAKE_WIDTH = 3
    OSM_CLEAR_WIDTH = 2
    OSM_CLEAR_WIDTH_TIGHT = 1
    OSM_CLEAR_TIGHT_POS = 50
    OSM_VOLUME_LIMIT = 30
    OSM_MAKE_EDGE = 2
    OSM_SKEW_UNIT = 24

    PEP_DRIFT = 0.100188
    PEP_CALIB_TICKS = 10
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
        cw = self.OSM_CLEAR_WIDTH_TIGHT if abs(pos) >= self.OSM_CLEAR_TIGHT_POS else self.OSM_CLEAR_WIDTH
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
        outer_asks = [p for p in d.sell_orders if p > fair + ask_edge - 1]
        outer_bids = [p for p in d.buy_orders if p < fair - bid_edge + 1]
        if outer_asks and outer_bids:
            baaf = min(outer_asks); bbbf = max(outer_bids)
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

        mid = (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0

        samples = td.get("_pep_samples", [])
        if tick < self.PEP_CALIB_TICKS:
            samples.append(mid - self.PEP_DRIFT * tick)
            td["_pep_samples"] = samples

        intercept = sum(samples) / len(samples) if samples else mid
        fair = intercept + self.PEP_DRIFT * tick

        need = lim - pos
        if need <= 0:
            return []

        orders = []
        bv = 0
        selective = tick < self.PEP_ENTRY_TIMEOUT
        threshold = fair + self.PEP_ENTRY_TAKE if selective else float("inf")

        for a in sorted(depth.sell_orders):
            if bv >= need:
                break
            if a > threshold:
                break
            vol = min(-depth.sell_orders[a], need - bv)
            if vol > 0:
                orders.append(Order(symbol, a, vol))
                bv += vol

        if selective and bv < need:
            competing = max(depth.buy_orders)
            target = max(self.PEP_BID_FLOOR, min(self.PEP_BID_CEIL, competing + 1 - int(round(fair))))
            orders.append(Order(symbol, int(round(fair)) + target, need - bv))

        return orders
