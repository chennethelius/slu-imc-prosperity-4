from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List


class Trader:
    """
    pepper_drift: test_5's pepper with one surgical change for a trending book.
      - OSMIUM unchanged (resin 3-phase).
      - Wall-mid FV kept (tracks trend well).
      - Position skew dropped 0.15 → 0.015 so we don't fight the drift.
        Tuned against the local backtester at --queue-penetration 0.0
        (realistic fill model — matches prosperity submission scale).
    """

    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    OSM_FAIR = 10000
    OSM_TAKE_WIDTH = 1
    OSM_CLEAR_WIDTH = 0
    OSM_VOLUME_LIMIT = 20

    PEP_POS_SKEW = 0.050

    def run(self, state: TradingState):
        result: dict[str, list[Order]] = {}
        for symbol, depth in state.order_depths.items():
            if symbol not in self.LIMITS:
                result[symbol] = []
                continue
            pos = state.position.get(symbol, 0)
            if symbol == "ASH_COATED_OSMIUM":
                result[symbol] = self._osmium(depth, pos)
            elif symbol == "INTARIAN_PEPPER_ROOT":
                result[symbol] = self._pepper(symbol, depth, pos)
            else:
                result[symbol] = []
        return result, 0, ""

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

    def _pepper(self, symbol, depth, pos):
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

        fv_eff = fv - pos * self.PEP_POS_SKEW
        fvi = int(round(fv_eff))

        lim = self.LIMITS["INTARIAN_PEPPER_ROOT"]
        orders = []
        br, sr = lim - pos, lim + pos

        for a in asks:
            if a < fv_eff - 1 and br > 0:
                v = min(-depth.sell_orders[a], br)
                orders.append(Order(symbol, a, v)); br -= v
            elif a < fv_eff and pos < 0 and br > 0:
                v = min(-depth.sell_orders[a], br, abs(pos))
                if v > 0:
                    orders.append(Order(symbol, a, v)); br -= v
            else:
                break

        for b in bids:
            if b > fv_eff + 1 and sr > 0:
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
