from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    """v21: slu_r2_submit osmium + v0_baseline pepper.

    Per-product winners on round2 data (QP=0.0, local backtester):

      Osmium — slu_r2_submit is the cleanest OOS winner at 3694
      (day -1), beating v15_adaptive_kalman (3171) by +523 SS.
      Training is 6068 (lowest), OOS is 3694 (highest): v15's richer
      Kalman + position skew overfit to in-sample volatility. slu's
      no-skew, MAKE_EDGE=3, static fair=10000 generalizes because it
      leaves wider buffers around an asset that mean-reverts sharply.

      Pepper — v0_baseline wins training (158513, v/s slu 157713) and
      ties OOS (79404 v/s slu 79406). v0's drift-detrended running-mean
      fair + 200-tick selective-take window captures the early-day
      inefficiency cleanly.

    Selection made strictly on training PnL per product with OOS
    cross-check (day -1) to catch overfitting. Training wins only
    matter when OOS agrees or is tied.
    """

    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    # slu osmium params
    OSM_FAIR_SEED = 10000
    OSM_TAKE_WIDTH = 3
    OSM_CLEAR_WIDTH = 2
    OSM_VOLUME_LIMIT = 30
    OSM_MAKE_EDGE = 3

    # v0 pepper params
    PEP_DRIFT = 0.100188
    PEP_ENTRY_TAKE = 7
    PEP_ENTRY_TIMEOUT = 200
    PEP_BID_FLOOR = -6
    PEP_BID_CEIL = 5

    def bid(self) -> int:
        return 20

    def run(self, state: TradingState):
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}
        result: dict[str, list[Order]] = {}
        for symbol, depth in state.order_depths.items():
            if symbol == "ASH_COATED_OSMIUM":
                result[symbol] = self._osmium(depth, state.position.get(symbol, 0))
            elif symbol == "INTARIAN_PEPPER_ROOT":
                result[symbol] = self._pepper(
                    symbol, depth, state.position.get(symbol, 0), state.timestamp, td
                )
            else:
                result[symbol] = []
        return result, 0, json.dumps(td)

    def _osmium(self, d, pos):
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
            cq = min(pos_after, sum(v for p, v in d.buy_orders.items() if p >= f_ask))
            sent = min(lim + pos - sv, cq)
            if sent > 0:
                orders.append(Order("ASH_COATED_OSMIUM", f_ask, -sent)); sv += sent
        elif pos_after < 0:
            cq = min(-pos_after, sum(-v for p, v in d.sell_orders.items() if p <= f_bid))
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
