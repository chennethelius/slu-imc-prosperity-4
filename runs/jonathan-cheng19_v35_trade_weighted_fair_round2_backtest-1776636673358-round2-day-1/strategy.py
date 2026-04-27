from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    """v35: blend trade-VWAP into the Kalman observation for osmium fair.

    v27's Kalman tracks microprice (volume-weighted top-of-book midpoint).
    Micro is a quote signal — where makers say they'll trade, biased by
    book imbalance. It doesn't see where actual aggressor-driven volume
    settled.

    state.market_trades gives executed fills during this tick. Their VWAP
    is an execution signal — where money actually changed hands. In
    liquid markets micro and trade-VWAP agree, but on ticks with strong
    aggressor flow the trade-VWAP can lead the quote-based signal.

    v35 feeds the Kalman a volume-weighted blend of micro and trade-VWAP
    as the observation for the innov update. Weights are the respective
    volumes themselves — zero arbitrary blending constants:

        micro_vol = bv_tob + av_tob
        trade_vol = sum of |qty| in this tick's trades
        obs = (micro * micro_vol + trade_vwap * trade_vol) /
              (micro_vol + trade_vol)

    When no trades this tick: obs = micro (v27 behavior).
    When heavy trade flow: obs shifts toward execution price.

    All other osmium logic is v27 verbatim — regime-gated CLEAR and MAKE
    skew, same envelope TAKE. Pepper unchanged. If trade-VWAP adds signal,
    the improved fair estimate tightens regime detection and CLEAR pricing;
    if not, the weighting reduces to micro-dominant on most ticks.
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
        osm_trades = state.market_trades.get("ASH_COATED_OSMIUM", []) if state.market_trades else []
        for symbol, depth in state.order_depths.items():
            if symbol == "ASH_COATED_OSMIUM":
                result[symbol] = self._osmium(depth, state.position.get(symbol, 0), td, osm_trades)
            elif symbol == "INTARIAN_PEPPER_ROOT":
                result[symbol] = self._pepper(
                    symbol, depth, state.position.get(symbol, 0), state.timestamp, td
                )
            else:
                result[symbol] = []
        return result, 0, json.dumps(td)

    def _osmium(self, d, pos, td, trades):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders)
        ba = min(d.sell_orders)
        bv_tob = d.buy_orders[bb]
        av_tob = -d.sell_orders[ba]
        micro_vol = bv_tob + av_tob
        micro = (bb * av_tob + ba * bv_tob) / micro_vol if micro_vol > 0 else (bb + ba) / 2.0

        trade_vol = 0
        trade_val = 0.0
        for t in trades:
            q = abs(t.quantity)
            trade_vol += q
            trade_val += t.price * q
        if trade_vol > 0 and micro_vol > 0:
            obs = (micro * micro_vol + trade_val) / (micro_vol + trade_vol)
        else:
            obs = micro

        fair = td.get("_osm_f", obs)
        innov = obs - fair
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
            bid_edge = ask_edge = max(1, self.OSM_MAKE_EDGE)
        else:
            bid_edge = max(1, self.OSM_MAKE_EDGE + skew)
            ask_edge = max(1, self.OSM_MAKE_EDGE - skew)
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
