from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    """
    Test_56 — passive pepper trend detection, reversible buy/short-and-hold.

    vs Test_55: during the first PEP_TREND_TICKS ticks, accumulate O(1)
    OLS moments on detrended mid (5 floats). At tick == PEP_TREND_TICKS
    we commit a direction:
        slope = (n·Σtm − Σt·Σm) / (n·Σtt − Σt²)
        pep_dir = -1 if slope < 0 else +1
    Before commit, pep_dir defaults to +1 (matches user's "initial
    assumption is still buy and hold"). Once committed, the pepper
    entry is mirrored symmetrically when pep_dir == -1: sell into the
    top-of-book bids, passive ask above fair, offset bounds negated.

    Expected on up-trending data: slope > 0 always, direction commits
    to +1, behavior byte-identical to Test_55.

    Params 13 → 14 (PEP_TREND_TICKS).
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
    PEP_TREND_TICKS = 30

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
        mid = (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0

        pep_sum = td.get("_pep_sum", 0.0) + mid - self.PEP_DRIFT * tick
        pep_cnt = td.get("_pep_cnt", 0) + 1
        td["_pep_sum"] = pep_sum
        td["_pep_cnt"] = pep_cnt

        fair = pep_sum / pep_cnt + self.PEP_DRIFT * tick
        fair_int = int(round(fair))

        pep_dir = td.get("_pep_dir", 0)
        if pep_dir == 0:
            t_n = td.get("_pep_tn", 0) + 1
            t_st = td.get("_pep_st", 0.0) + tick
            t_sm = td.get("_pep_sm", 0.0) + mid
            t_stm = td.get("_pep_stm", 0.0) + tick * mid
            t_stt = td.get("_pep_stt", 0.0) + tick * tick
            if t_n >= self.PEP_TREND_TICKS:
                denom = t_n * t_stt - t_st * t_st
                slope = (t_n * t_stm - t_st * t_sm) / denom if denom > 0 else 0.0
                td["_pep_dir"] = -1 if slope < 0 else 1
                for k in ("_pep_tn", "_pep_st", "_pep_sm", "_pep_stm", "_pep_stt"):
                    td.pop(k, None)
                pep_dir = td["_pep_dir"]
            else:
                td["_pep_tn"] = t_n
                td["_pep_st"] = t_st
                td["_pep_sm"] = t_sm
                td["_pep_stm"] = t_stm
                td["_pep_stt"] = t_stt
                pep_dir = 1

        cap = lim - pep_dir * pos
        if cap <= 0:
            return []

        orders = []
        filled = 0
        selective = tick < self.PEP_ENTRY_TIMEOUT

        if pep_dir > 0:
            threshold = fair + self.PEP_ENTRY_TAKE if selective else float("inf")
            for a in sorted(depth.sell_orders):
                if filled >= cap or a > threshold:
                    break
                vol = min(-depth.sell_orders[a], cap - filled)
                if vol > 0:
                    orders.append(Order(symbol, a, vol))
                    filled += vol
            if selective and filled < cap:
                competing = max(depth.buy_orders)
                offset = max(self.PEP_BID_FLOOR, min(self.PEP_BID_CEIL, competing + 1 - fair_int))
                orders.append(Order(symbol, fair_int + offset, cap - filled))
        else:
            threshold = fair - self.PEP_ENTRY_TAKE if selective else float("-inf")
            for b in sorted(depth.buy_orders, reverse=True):
                if filled >= cap or b < threshold:
                    break
                vol = min(depth.buy_orders[b], cap - filled)
                if vol > 0:
                    orders.append(Order(symbol, b, -vol))
                    filled += vol
            if selective and filled < cap:
                competing = min(depth.sell_orders)
                offset = max(-self.PEP_BID_CEIL, min(-self.PEP_BID_FLOOR, competing - 1 - fair_int))
                orders.append(Order(symbol, fair_int + offset, -(cap - filled)))

        return orders
