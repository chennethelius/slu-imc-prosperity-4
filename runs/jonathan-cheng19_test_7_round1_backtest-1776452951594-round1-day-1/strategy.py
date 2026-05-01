from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    """
    Test_7 — fixes Test_6's two big leaks:

    PEPPER (INTARIAN_PEPPER_ROOT): the 50-tick slope detector oscillated between
    +/-70 targets (trigger 0.005 vs slope std ~0.44), burning ~16 of spread per
    flip * thousands of flips = catastrophic loss. Replaced with a
    drift-aware passive loader: known positive drift of ~0.1/tick => just hold
    max long, but enter passively on the bid (no ask-taking) to avoid paying
    spread. Once at +limit, rest one layer passive to harvest any mean-reversion
    fills without giving up drift exposure.

    OSMIUM (ASH_COATED_OSMIUM): fair=10000, noise std ~5.3 per earlier
    calibration -> TAKE_WIDTH=1 triggered on in-distribution noise and was
    adversely selected. Widened to 3 on take, 2 on clear, and push make quotes
    to edge 3+ (not 2). Same three-phase template otherwise.
    """

    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    OSM_FAIR = 10000
    OSM_TAKE_WIDTH = 3
    OSM_CLEAR_WIDTH = 2
    OSM_VOLUME_LIMIT = 30
    OSM_MAKE_EDGE = 3

    PEP_TARGET = 80  # hold max long given known positive drift

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
                result[symbol] = self._pepper(symbol, depth, pos)
            else:
                result[symbol] = []
        return result, 0, json.dumps(td)

    # ---- OSMIUM: widened three-phase ----
    def _osmium(self, d, pos):
        if not d.buy_orders or not d.sell_orders:
            return []
        fair = self.OSM_FAIR
        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        tw = self.OSM_TAKE_WIDTH
        cw = self.OSM_CLEAR_WIDTH
        vol_lim = self.OSM_VOLUME_LIMIT
        edge = self.OSM_MAKE_EDGE
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

    # ---- PEPPER: passive drift loader ----
    def _pepper(self, symbol, depth, pos):
        if not depth.buy_orders or not depth.sell_orders:
            return []
        lim = self.LIMITS["INTARIAN_PEPPER_ROOT"]
        target = self.PEP_TARGET
        orders = []

        bb = max(depth.buy_orders)
        ba = min(depth.sell_orders)
        if bb >= ba:
            return []

        # Any ask strictly below best-bid+1 is free money (rare) — take it.
        need = target - pos
        if need > 0:
            asks = sorted(depth.sell_orders)
            to_buy = min(need, lim - pos)
            for a in asks:
                if a > bb:
                    break
                vol = min(-depth.sell_orders[a], to_buy)
                if vol > 0:
                    orders.append(Order(symbol, a, vol)); to_buy -= vol
                    if to_buy <= 0:
                        break

            # Rest the remainder passively one tick above best bid (still
            # penny-inside the spread — pepper's wide book makes this viable).
            if to_buy > 0:
                orders.append(Order(symbol, bb + 1, to_buy))

        # No sell-side logic: drift is positive, we want to hold. If position
        # somehow exceeds target (shouldn't happen), do nothing special.
        return orders
