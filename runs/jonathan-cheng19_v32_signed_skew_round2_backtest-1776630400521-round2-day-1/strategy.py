from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    """v32: signed-skew MAKE in favorable regime — press into drift.

    v27 (current CI champion, 199176) makes MAKE symmetric narrow when
    favorable_inv fires: bid_edge = ask_edge = MAKE_EDGE. Balanced —
    fills both sides equally likely, relies on market noise.

    v32 commits to the regime signal. Instead of zero-skew on favorable,
    use FLIPPED skew: same magnitude as v22's unwind skew, but direction
    reversed to accumulate (not shed) drift-favorable inventory.

    v22 adverse (long, skew=5):
        bid_edge = 1 + 5 = 6   (wide bid — block more long)
        ask_edge = max(1, 1-5) = 1   (tight ask — push sells)
    v27 favorable (long, skew=5):
        bid_edge = ask_edge = 1   (neutral, rely on market)
    v32 favorable (long, skew=5):
        flipped = -5
        bid_edge = max(1, 1 + (-5)) = 1   (tight bid — easy to add long)
        ask_edge = 1 - (-5) = 6   (wide ask — preserve long)

    When long+favorable (fair<STATIC, expecting rise), tight bid fills
    more easily → accumulates more drift-favorable inventory. Wide ask
    refuses to sell cheap → preserves for MtM at close. Symmetric for
    short+favorable. The hard position cap (±80) is the ultimate safety.

    Zero new parameters. Same skew magnitude as v22 — only the sign
    changes based on regime. CLEAR gate (v26) and TAKE (v22) unchanged.

    Risk trade-off: v32 is more committed to the mean-reversion thesis
    than v27. If fair<STATIC is a false regime (not actually reverting),
    we'd over-accumulate and pay MtM loss at close. Osmium's historical
    osmiium-mean adherence to STATIC=10001 makes this a reasonable bet.
    v28 (catastrophic -11255) failed by killing fill rate; v32 keeps
    queue-jumping intact — only shifts edge widths by same mechanism v22
    already uses, in a direction the data suggests is profitable.
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
        return 0

    def run(self, state: TradingState):
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}
        result: dict[str, list[Order]] = {}
        for symbol, depth in state.order_depths.items():
            if symbol == "ASH_COATED_OSMIUM":
                result[symbol] = self._osmium(depth, state.position.get(symbol, 0), td)
            elif symbol == "INTARIAN_PEPPER_ROOT":
                result[symbol] = self._pepper(
                    symbol, depth, state.position.get(symbol, 0), state.timestamp, td
                )
            else:
                result[symbol] = []
        return result, 0, json.dumps(td)

    def _osmium(self, d, pos, td):
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
            make_skew = -skew
        else:
            make_skew = skew
        bid_edge = max(1, self.OSM_MAKE_EDGE + make_skew)
        ask_edge = max(1, self.OSM_MAKE_EDGE - make_skew)
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
