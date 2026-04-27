"""
disc_osm_alpha: Test_9_e3's pepper logic + aggressive OSMIUM MM.

After backtesting all variants, finding: PEPPER is drift-capped at ~79k
(pure BnH floor). All alpha differentiation is in OSMIUM (range 3-4k).

This strategy keeps Test_9_e3's proven pepper (aggressive take up to mid+6,
hold +80) and experiments with tighter OSMIUM MM:
- EWMA fair (α=0.05)
- Tight MM edge (1 instead of 3) to capture more spread
- Take width 2 (was 3) to catch more edges
- Aggressive clear at fair edge when over-leveraged

Anti-overfit:
- Parameters are symmetric (no long/short bias on OSMIUM which mean-reverts)
- EWMA alpha chosen to match observed ~20-tick reversion timescale
- No per-day tuning
"""
from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    # PEPPER (from Test_9_e3, proven)
    PEP_TARGET = 80
    PEP_TAKE_DEPTH = 6

    # OSMIUM (aggressive)
    OSM_FAIR_SEED = 10000
    OSM_EWMA_ALPHA = 0.05
    OSM_TAKE_WIDTH = 2       # narrower than Test_9's 3
    OSM_CLEAR_WIDTH = 1      # tight clear
    OSM_MAKE_EDGE = 1        # aggressive MM (was 3 in Test_9)
    OSM_VOL_LIMIT = 30

    def bid(self):
        return 15

    def run(self, state: TradingState):
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}

        result: dict[str, list[Order]] = {}
        for symbol, depth in state.order_depths.items():
            pos = state.position.get(symbol, 0)
            if symbol == "ASH_COATED_OSMIUM":
                result[symbol] = self._osmium(depth, pos, td)
            elif symbol == "INTARIAN_PEPPER_ROOT":
                result[symbol] = self._pepper(symbol, depth, pos)
            else:
                result[symbol] = []
        return result, 0, json.dumps(td)

    def _osmium(self, d, pos, td):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders)
        ba = min(d.sell_orders)
        if bb >= ba:
            return []
        mid = (bb + ba) / 2.0

        fair = td.get("osm_fair", self.OSM_FAIR_SEED)
        fair = (1 - self.OSM_EWMA_ALPHA) * fair + self.OSM_EWMA_ALPHA * mid
        td["osm_fair"] = fair

        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        tw = self.OSM_TAKE_WIDTH
        cw = self.OSM_CLEAR_WIDTH
        edge = self.OSM_MAKE_EDGE
        vol_lim = self.OSM_VOL_LIMIT

        orders = []
        bv = sv = 0

        # Take cheap asks
        ba_amt = -d.sell_orders[ba]
        if ba <= fair - tw:
            q = min(ba_amt, lim - pos - bv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", ba, q))
                bv += q
        # Take rich bids
        bb_amt = d.buy_orders[bb]
        if bb >= fair + tw:
            q = min(bb_amt, lim + pos - sv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", bb, -q))
                sv += q

        # Clear inventory through fair edges
        pos_after = pos + bv - sv
        f_bid = int(fair - cw)
        f_ask = int(fair + cw)
        if pos_after > 0:
            cq = sum(v for p, v in d.buy_orders.items() if p >= f_ask)
            cq = min(cq, pos_after, lim + pos - sv)
            if cq > 0:
                orders.append(Order("ASH_COATED_OSMIUM", f_ask, -cq))
                sv += cq
        if pos_after < 0:
            cq = sum(-v for p, v in d.sell_orders.items() if p <= f_bid)
            cq = min(cq, -pos_after, lim - pos - bv)
            if cq > 0:
                orders.append(Order("ASH_COATED_OSMIUM", f_bid, cq))
                bv += cq

        # Passive MM inside existing book
        baaf = [p for p in d.sell_orders if p > fair + edge - 1]
        bbbf = [p for p in d.buy_orders if p < fair - edge + 1]
        if baaf and bbbf:
            baaf_min = min(baaf)
            bbbf_max = max(bbbf)
            if baaf_min <= fair + edge and pos <= vol_lim:
                baaf_min = fair + edge + 1
            if bbbf_max >= fair - edge and pos >= -vol_lim:
                bbbf_max = fair - edge - 1
            bid_p = bbbf_max + 1
            ask_p = baaf_min - 1
            buy_q = max(0, lim - pos - bv)
            sell_q = max(0, lim + pos - sv)
            if buy_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(bid_p), buy_q))
            if sell_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(ask_p), -sell_q))
        return orders

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
                orders.append(Order(symbol, a, vol))
                to_buy -= vol
                if to_buy <= 0:
                    break
        if to_buy > 0:
            orders.append(Order(symbol, bb + 1, to_buy))
        return orders
