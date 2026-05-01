from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    """v37: detect adverse-selection on prior fill, widen MAKE edge next tick.

    v27 (CI champion 199176) posts MAKE at fair±edge. When a market taker
    lifts our ask or hits our bid, they often have better short-term
    information — classic adverse selection for market makers. We sell at
    10001, fair drifts to 10002 next tick; the fill was toxic.

    No prior variant looked at state.own_trades to measure this. v37 does:

        For each own fill last tick, record the execution price per side.
        At current tick, after the Kalman update:
            bid_adv  = last_buy_px  is not None AND fair < last_buy_px
            ask_adv  = last_sell_px is not None AND fair > last_sell_px
        If adverse on a side, double that side's MAKE edge this tick:
            if bid_adv: bid_edge *= 2
            if ask_adv: ask_edge *= 2

    The fill records are consumed (cleared) after one check, so the widen
    is a one-tick defensive response, not a persistent penalty. Once fair
    stabilizes, bid_adv/ask_adv flip false and edges return to v27 normal.

    Zero new numerical thresholds — \"adverse\" is literally fair having
    moved past the fill price. The 2× multiplier is the smallest non-
    trivial structural widening (1 would be no-op, since a +1 increment
    could be a param choice; 2× is multiplicative structural).

    TAKE, CLEAR gate, regime logic, pepper all unchanged. This only
    touches the MAKE-edge calculation in the branch v27 already uses.
    """

    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    OSM_K_SS = 0.1353
    OSM_FAIR_STATIC = 10001
    OSM_TAKE_WIDTH = 2
    OSM_CLEAR_WIDTH = 2
    OSM_VOLUME_LIMIT = 30
    OSM_MAKE_EDGE = 1
    OSM_SKEW_UNIT = 12

    PEP_DRIFT = 0.100188
    PEP_ENTRY_TAKE = 7
    PEP_ENTRY_TIMEOUT = 200
    PEP_BID_FLOOR = -6
    PEP_BID_CEIL = 5

    def bid(self) -> int:
        return 1103

    def run(self, state: TradingState):
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}
        result: dict[str, list[Order]] = {}
        own_osm = state.own_trades.get("ASH_COATED_OSMIUM", []) if state.own_trades else []
        for symbol, depth in state.order_depths.items():
            if symbol == "ASH_COATED_OSMIUM":
                result[symbol] = self._osmium(depth, state.position.get(symbol, 0), td, own_osm)
            elif symbol == "INTARIAN_PEPPER_ROOT":
                result[symbol] = self._pepper(
                    symbol, depth, state.position.get(symbol, 0), state.timestamp, td
                )
            else:
                result[symbol] = []
        return result, 0, json.dumps(td)

    def _osmium(self, d, pos, td, own):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders)
        ba = min(d.sell_orders)
        bv_tob = d.buy_orders[bb]
        av_tob = -d.sell_orders[ba]
        tot = bv_tob + av_tob
        micro = (bb * av_tob + ba * bv_tob) / tot if tot > 0 else (bb + ba) / 2.0

        fair = td.get("_osm_f", micro)
        innov = micro - fair
        err_ema = td.get("_osm_err", abs(innov))
        err_ema += self.OSM_K_SS * (abs(innov) - err_ema)
        td["_osm_err"] = err_ema
        fair += (self.OSM_K_SS / (1.0 + err_ema)) * innov
        td["_osm_f"] = fair

        lbp = td.get("_lbp")
        lsp = td.get("_lsp")
        bid_adv = lbp is not None and fair < lbp
        ask_adv = lsp is not None and fair > lsp

        new_lbp = None
        new_lsp = None
        for t in own:
            if t.buyer == "SUBMISSION":
                new_lbp = t.price if new_lbp is None else max(new_lbp, t.price)
            if t.seller == "SUBMISSION":
                new_lsp = t.price if new_lsp is None else min(new_lsp, t.price)
        td["_lbp"] = new_lbp
        td["_lsp"] = new_lsp

        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        static = self.OSM_FAIR_STATIC
        cw = self.OSM_CLEAR_WIDTH
        orders = []
        bv = sv = 0

        skew = round(pos / self.OSM_SKEW_UNIT)
        ask_limit = max(static, fair) - max(0, self.OSM_TAKE_WIDTH + skew)
        bid_limit = min(static, fair) + max(0, self.OSM_TAKE_WIDTH - skew)
        for a in sorted(d.sell_orders):
            if a > ask_limit:
                break
            q = min(-d.sell_orders[a], lim - pos - bv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", a, q)); bv += q
        for b in sorted(d.buy_orders, reverse=True):
            if b < bid_limit:
                break
            q = min(d.buy_orders[b], lim + pos - sv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", b, -q)); sv += q

        pos_after = pos + bv - sv
        f_bid = int(round(fair - cw))
        f_ask = int(round(fair + cw))
        long_favorable = fair < static
        short_favorable = fair > static
        if pos_after > 0 and not long_favorable:
            cq = min(pos_after, sum(v for p, v in d.buy_orders.items() if p >= f_ask))
            sent = min(lim + pos - sv, cq)
            if sent > 0:
                orders.append(Order("ASH_COATED_OSMIUM", f_ask, -sent)); sv += sent
        elif pos_after < 0 and not short_favorable:
            cq = min(-pos_after, sum(-v for p, v in d.sell_orders.items() if p <= f_bid))
            sent = min(lim - pos - bv, cq)
            if sent > 0:
                orders.append(Order("ASH_COATED_OSMIUM", f_bid, sent)); bv += sent

        favorable_inv = (pos > 0 and long_favorable) or (pos < 0 and short_favorable)
        if favorable_inv:
            bid_edge = ask_edge = max(1, self.OSM_MAKE_EDGE)
        else:
            bid_edge = max(1, self.OSM_MAKE_EDGE + skew)
            ask_edge = max(1, self.OSM_MAKE_EDGE - skew)
        if bid_adv:
            bid_edge *= 2
        if ask_adv:
            ask_edge *= 2
        baaf = min((p for p in d.sell_orders if p > fair + ask_edge - 1), default=None)
        bbbf = max((p for p in d.buy_orders if p < fair - bid_edge + 1), default=None)
        if baaf is not None and bbbf is not None:
            if baaf <= fair + ask_edge and pos <= self.OSM_VOLUME_LIMIT:
                baaf = int(round(fair + ask_edge + 1))
            if bbbf >= fair - bid_edge and pos >= -self.OSM_VOLUME_LIMIT:
                bbbf = int(round(fair - bid_edge - 1))
            buy_q = lim - pos - bv
            if buy_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", bbbf + 1, buy_q))
            sell_q = lim + pos - sv
            if sell_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", baaf - 1, -sell_q))
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
