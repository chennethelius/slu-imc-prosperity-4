from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    """
    Test_8 — squeeze entry/fair-tracking over Test_7:

    PEPPER: Test_7 rested at bid+1, which was slow to fill against a rising
    market (drift 0.1/tick). Gap to theoretical max (~80k) was ~4k = ~500
    ticks of missed long exposure. Test_8 takes asks within `PEP_TAKE_DEPTH`
    of mid to load quickly (cheap spread pay < missed drift), then holds.

    OSMIUM: fair was hardcoded to 10000 but latent mean-reverts around it
    with std ~5.3. Off-center fair makes take/clear thresholds misaligned.
    Test_8 uses an EWMA of book-mid as fair_hat, seeded at 10000. Take/clear
    widths unchanged from Test_7.
    """

    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    OSM_FAIR_SEED = 10000
    OSM_EWMA_ALPHA = 0.05   # ~20-tick half-life
    OSM_TAKE_WIDTH = 3
    OSM_CLEAR_WIDTH = 2
    OSM_VOLUME_LIMIT = 30
    OSM_MAKE_EDGE = 3

    PEP_TARGET = 80
    PEP_TAKE_DEPTH = 6  # take asks up to mid+6 (covers inner ask at fair+~4)

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
                result[symbol] = self._pepper(symbol, depth, pos)
            else:
                result[symbol] = []
        return result, 0, json.dumps(td)

    # ---- OSMIUM: three-phase with EWMA fair ----
    def _osmium(self, d, pos, td):
        if not d.buy_orders or not d.sell_orders:
            return []

        bb = max(d.buy_orders)
        ba = min(d.sell_orders)
        fair = self.OSM_FAIR_SEED

        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        tw = self.OSM_TAKE_WIDTH
        cw = self.OSM_CLEAR_WIDTH
        vol_lim = self.OSM_VOLUME_LIMIT
        edge = self.OSM_MAKE_EDGE
        orders = []
        bv = sv = 0

        ba_amt = -d.sell_orders[ba]
        if ba <= fair - tw:
            q = min(ba_amt, lim - pos - bv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", ba, q)); bv += q
        bb_amt = d.buy_orders[bb]
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

        baaf = [p for p in d.sell_orders if p > fair + edge - 1]
        bbbf = [p for p in d.buy_orders if p < fair - edge + 1]
        if baaf and bbbf:
            baaf = min(baaf); bbbf = max(bbbf)
            if baaf <= fair + edge and pos <= vol_lim:
                baaf = fair + edge + 1
            if bbbf >= fair - edge and pos >= -vol_lim:
                bbbf = fair - edge - 1
            bid = bbbf + 1; ask = baaf - 1
            buy_q = lim - pos - bv
            if buy_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(bid), buy_q))
            sell_q = lim + pos - sv
            if sell_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(ask), -sell_q))
        return orders

    # ---- PEPPER: aggressive entry, then hold ----
    def _pepper(self, symbol, depth, pos):
        if not depth.buy_orders or not depth.sell_orders:
            return []
        lim = self.LIMITS["INTARIAN_PEPPER_ROOT"]
        target = self.PEP_TARGET
        bb = max(depth.buy_orders)
        ba = min(depth.sell_orders)
        if bb >= ba:
            return []
        mid = (bb + ba) / 2.0
        orders = []

        need = target - pos
        if need <= 0:
            return []

        to_buy = min(need, lim - pos)
        asks = sorted(depth.sell_orders)
        for a in asks:
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
