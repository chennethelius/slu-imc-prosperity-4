from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    """
    Test_29 — EWMA-calibrated entry only (no MM), over Test_24.

    Uses 10-tick EWMA to estimate intercept with σ/√10 ≈ 0.69 accuracy.
    Better fair estimate → more precise entry price caps. Same hybrid entry
    as Test_24 but with calibrated fair instead of first-tick anchor.
    No pepper MM phase — pure buy-and-hold after reaching target.
    """

    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    OSM_FAIR = 10000
    OSM_TAKE_WIDTH = 3
    OSM_CLEAR_WIDTH = 2
    OSM_VOLUME_LIMIT = 30
    OSM_MAKE_EDGE = 3
    OSM_MIN_EDGE = 1
    OSM_SKEW_UNIT = 24
    OSM_MIN_TW = 0

    PEP_TARGET = 80
    PEP_DRIFT = 0.100188
    PEP_CALIB_TICKS = 10
    PEP_ENTRY_TAKE = 7
    PEP_ENTRY_BID = 5
    PEP_ENTRY_TIMEOUT = 200

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
                result[symbol] = self._pepper(symbol, depth, pos, state.timestamp, td)
            else:
                result[symbol] = []
        return result, 0, json.dumps(td)

    def _osmium(self, d, pos):
        if not d.buy_orders or not d.sell_orders:
            return []
        fair = self.OSM_FAIR
        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        cw = self.OSM_CLEAR_WIDTH
        vol_lim = self.OSM_VOLUME_LIMIT
        orders = []
        bv = sv = 0

        skew = round(pos / self.OSM_SKEW_UNIT)
        tw_ask = max(self.OSM_MIN_TW, self.OSM_TAKE_WIDTH + skew)
        tw_bid = max(self.OSM_MIN_TW, self.OSM_TAKE_WIDTH - skew)

        ba = min(d.sell_orders); ba_amt = -d.sell_orders[ba]
        if ba <= fair - tw_ask:
            q = min(ba_amt, lim - pos - bv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", ba, q)); bv += q
        bb = max(d.buy_orders); bb_amt = d.buy_orders[bb]
        if bb >= fair + tw_bid:
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

        bid_edge = max(self.OSM_MIN_EDGE, self.OSM_MAKE_EDGE + skew)
        ask_edge = max(self.OSM_MIN_EDGE, self.OSM_MAKE_EDGE - skew)
        outer_asks = [p for p in d.sell_orders if p > fair + ask_edge - 1]
        outer_bids = [p for p in d.buy_orders if p < fair - bid_edge + 1]
        if outer_asks and outer_bids:
            baaf = min(outer_asks); bbbf = max(outer_bids)
            if baaf <= fair + ask_edge and pos <= vol_lim:
                baaf = fair + ask_edge + 1
            if bbbf >= fair - bid_edge and pos >= -vol_lim:
                bbbf = fair - bid_edge - 1
            bid = bbbf + 1; ask = baaf - 1
            buy_q = lim - pos - bv
            if buy_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(bid), buy_q))
            sell_q = lim + pos - sv
            if sell_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(ask), -sell_q))
        return orders

    def _pepper(self, symbol, depth, pos, timestamp, td):
        if not depth.buy_orders or not depth.sell_orders:
            return []
        lim = self.LIMITS["INTARIAN_PEPPER_ROOT"]
        tick = timestamp // 100

        bb = max(depth.buy_orders)
        ba = min(depth.sell_orders)
        mid = (bb + ba) / 2.0

        samples = td.get("_pep_samples", [])
        if tick < self.PEP_CALIB_TICKS:
            samples.append(mid - self.PEP_DRIFT * tick)
            td["_pep_samples"] = samples

        if samples:
            intercept = sum(samples) / len(samples)
        else:
            intercept = mid
        fair = intercept + self.PEP_DRIFT * tick

        need = self.PEP_TARGET - pos
        if need <= 0:
            return []

        to_buy = min(need, lim - pos)
        bv = 0
        orders = []
        selective = tick < self.PEP_ENTRY_TIMEOUT

        if selective:
            for a in sorted(depth.sell_orders):
                if bv >= to_buy:
                    break
                if a <= fair + self.PEP_ENTRY_TAKE:
                    vol = min(-depth.sell_orders[a], to_buy - bv)
                    if vol > 0:
                        orders.append(Order(symbol, a, vol))
                        bv += vol
            remaining = to_buy - bv
            if remaining > 0:
                bid_price = int(fair + self.PEP_ENTRY_BID)
                orders.append(Order(symbol, bid_price, remaining))
        else:
            for a in sorted(depth.sell_orders):
                if bv >= to_buy:
                    break
                vol = min(-depth.sell_orders[a], to_buy - bv)
                if vol > 0:
                    orders.append(Order(symbol, a, vol))
                    bv += vol

        return orders
