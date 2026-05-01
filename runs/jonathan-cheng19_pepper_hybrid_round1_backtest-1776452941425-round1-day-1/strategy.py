from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List
import json


class Trader:
    """
    pepper_hybrid: MM + trend on pepper, OSM unchanged.
      - Pepper inventory target drifts with wall-mid slope (last N ticks).
      - Skew anchors to target, not zero — when trending up, we WANT long.
      - Asymmetric take: loosen on trend side, tighten on fade side.
      - Trend signal persisted via trader_data (window of wall-mids).
    """

    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    OSM_FAIR = 10000
    OSM_TAKE_WIDTH = 1
    OSM_CLEAR_WIDTH = 0
    OSM_VOLUME_LIMIT = 20

    PEP_POS_SKEW = 0.015
    PEP_WINDOW = 100
    PEP_TARGET_K = 200.0
    PEP_TARGET_CAP = 60
    PEP_TREND_ON = 10

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
        bids = sorted(depth.buy_orders.keys(), reverse=True)
        asks = sorted(depth.sell_orders.keys())
        bb, ba = bids[0], asks[0]
        if bb >= ba:
            return []

        bid_wall = min(depth.buy_orders.keys())
        ask_wall = max(depth.sell_orders.keys())
        fv = (bid_wall + ask_wall) / 2.0

        hist = td.get("pep_hist", [])
        hist.append(fv)
        if len(hist) > self.PEP_WINDOW:
            hist = hist[-self.PEP_WINDOW:]
        td["pep_hist"] = hist

        if len(hist) >= self.PEP_WINDOW:
            slope = (hist[-1] - hist[0]) / len(hist)
            target = max(-self.PEP_TARGET_CAP, min(self.PEP_TARGET_CAP, slope * self.PEP_TARGET_K))
        else:
            target = 0.0

        fv_eff = fv - (pos - target) * self.PEP_POS_SKEW
        fvi = int(round(fv_eff))

        buy_take_off = 0 if target > self.PEP_TREND_ON else -1
        sell_take_off = 0 if target < -self.PEP_TREND_ON else 1

        lim = self.LIMITS["INTARIAN_PEPPER_ROOT"]
        orders = []
        br, sr = lim - pos, lim + pos

        for a in asks:
            if a < fv_eff + buy_take_off and br > 0:
                v = min(-depth.sell_orders[a], br)
                orders.append(Order(symbol, a, v)); br -= v
            elif a < fv_eff and pos < 0 and br > 0:
                v = min(-depth.sell_orders[a], br, abs(pos))
                if v > 0:
                    orders.append(Order(symbol, a, v)); br -= v
            else:
                break

        for b in bids:
            if b > fv_eff + sell_take_off and sr > 0:
                v = min(depth.buy_orders[b], sr)
                orders.append(Order(symbol, b, -v)); sr -= v
            elif b > fv_eff and pos > 0 and sr > 0:
                v = min(depth.buy_orders[b], sr, pos)
                if v > 0:
                    orders.append(Order(symbol, b, -v)); sr -= v
            else:
                break

        best_bid_vol = depth.buy_orders.get(bb, 0)
        best_ask_vol = abs(depth.sell_orders.get(ba, 0))
        if best_bid_vol > 1:
            bp = min(bb + 1, fvi - 1)
        else:
            bp = min(bb, fvi - 1)
        if best_ask_vol > 1:
            ap = max(ba - 1, fvi + 1)
        else:
            ap = max(ba, fvi + 1)

        if br > 0:
            orders.append(Order(symbol, bp, br))
        if sr > 0:
            orders.append(Order(symbol, ap, -sr))
        return orders
