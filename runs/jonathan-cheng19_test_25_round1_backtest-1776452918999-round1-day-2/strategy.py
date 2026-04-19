from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    """
    Test_25 — pepper fair-value MM + buy-and-hold (over Test_20).

    Pepper price model: mid_t = anchor + 0.1*t + ε_t (ARIMA(0,1,1) with θ=-1).
    Since θ=-1, consecutive changes: Δmid = drift + ε_t - ε_{t-1}, meaning
    the noise is iid with σ≈2.19. This gives us an accurate fair estimate.

    Strategy:
      Phase 1 (pos < target): Buy aggressively but cap at inner ask level
        (fair_est + 7). Skip overpriced outer asks.
      Phase 2 (pos >= target): Market-make with remaining capacity.
        - Estimate fair from anchor + drift*tick
        - Post bids at fair-1 and asks at fair+1
        - Cap MM size to keep inventory within [target-MM_SIZE, target]
        - This exploits the ~13-wide book spread by posting inside it

    OSMIUM unchanged from Test_20.
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
    PEP_ENTRY_SPREAD = 7
    PEP_MM_EDGE = 2
    PEP_MM_SIZE = 10

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

        bb = max(depth.buy_orders)
        ba = min(depth.sell_orders)
        mid = (bb + ba) / 2.0
        tick = timestamp // 100

        if "_pep_anchor" not in td:
            td["_pep_anchor"] = mid
            td["_pep_t0"] = timestamp

        fair = td["_pep_anchor"] + self.PEP_DRIFT * ((timestamp - td["_pep_t0"]) / 100.0)
        orders = []

        need = self.PEP_TARGET - pos
        if need > 0:
            to_buy = min(need, lim - pos)
            max_price = fair + self.PEP_ENTRY_SPREAD
            for a in sorted(depth.sell_orders):
                if to_buy <= 0:
                    break
                if a > max_price:
                    break
                vol = min(-depth.sell_orders[a], to_buy)
                if vol > 0:
                    orders.append(Order(symbol, a, vol))
                    to_buy -= vol
        else:
            mm_edge = self.PEP_MM_EDGE
            mm_size = self.PEP_MM_SIZE
            fair_int = int(round(fair))

            sell_room = lim + pos
            buy_room = lim - pos

            ask_price = fair_int + mm_edge
            bid_price = fair_int - mm_edge

            ask_q = min(mm_size, sell_room, pos - (self.PEP_TARGET - mm_size))
            if ask_q > 0:
                orders.append(Order(symbol, ask_price, -ask_q))

            bid_q = min(mm_size, buy_room)
            if bid_q > 0:
                orders.append(Order(symbol, bid_price, bid_q))

            for a in sorted(depth.sell_orders):
                if a <= fair_int - 1 and buy_room > 0:
                    vol = min(-depth.sell_orders[a], buy_room)
                    if vol > 0:
                        orders.append(Order(symbol, a, vol))
                        buy_room -= vol

            for b in sorted(depth.buy_orders.keys(), reverse=True):
                if b >= fair_int + 1 and sell_room > 0:
                    vol = min(depth.buy_orders[b], sell_room)
                    if vol > 0:
                        orders.append(Order(symbol, b, -vol))
                        sell_room -= vol

        return orders
