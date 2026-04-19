from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    """v16 (lean): Glosten-Milgrom own-trade adverse-selection for osmium.

    When our SELL fills, an aggressive buyer hit our ask — revealing
    bullish information. Symmetric for own BUYs. We shift micro by
    +/-1 tick on sign before feeding the Kalman, so a single fill
    produces a ~0.14-tick fair nudge (K_SS * 1.0) that the |innov|
    damping can discount in noisy regimes.

    MC stats (paired same-seed per-session delta vs Test_88/v14):
      access:   mean=+145.6 (std 383, t=+8.51, 66.2% wins)
      noaccess: mean=+181.9 (std 358, t=+11.37, 72.6% wins)

    Paired t strips out market-shock variance (same seed, same FV path,
    same market-trade timing — only strategy actions differ), so the
    small absolute delta is decisively significant (p < 1e-15).

    Dead-ends ruled out (pairwise vs v16, 500 paired sessions):
      - Market-trade aggressor flow: additive t=+0.00 (byte-identical)
      - Book-pressure derivative:    t=+0.79
      - OFI (Cont/Kukanov):          t=-0.06
      - Magnitude-scaled adv ±3:     t=+0.09
      - Adverse EMA (persistence):   t=-13.85 (catastrophic)
      - Confidence-damped adv:       t=-1.44
      - One-sided vs two-sided flow: t=-0.16
      - Avellaneda-Stoikov MAKE_EDGE: -19 SEM (widening murders fills)

    Round 2 strategy:
      * Osmium — mean-reverting MM around fair=10001, adaptive-K Kalman
                 microprice + Glosten-Milgrom own-trade shift.
      * Pepper — buy-and-hold capturing +0.099977/tick ARIMA drift.
    """

    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    OSM_K_SS = 0.1353
    OSM_FAIR_STATIC = 10001
    OSM_TAKE_WIDTH = 2
    OSM_CLEAR_WIDTH = 2
    OSM_VOLUME_LIMIT = 30
    OSM_MAKE_EDGE = 1
    OSM_SKEW_UNIT = 12

    PEP_DRIFT = 0.099977
    PEP_ENTRY_TAKE = 5
    PEP_BID_FLOOR = -6

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
                result[symbol] = self._osmium(
                    depth,
                    state.position.get(symbol, 0),
                    td,
                    state.own_trades.get(symbol, []),
                )
            elif symbol == "INTARIAN_PEPPER_ROOT":
                result[symbol] = self._pepper(
                    symbol, depth, state.position.get(symbol, 0), state.timestamp, td
                )
            else:
                result[symbol] = []
        return result, 0, json.dumps(td)

    def _osmium(self, d, pos, td, own_trades):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders)
        ba = min(d.sell_orders)
        bv_tob = d.buy_orders[bb]
        av_tob = -d.sell_orders[ba]
        tot = bv_tob + av_tob
        micro = (bb * av_tob + ba * bv_tob) / tot if tot > 0 else (bb + ba) / 2.0

        adv = 0
        for t in own_trades:
            if t.seller == "SUBMISSION":
                adv += t.quantity
            elif t.buyer == "SUBMISSION":
                adv -= t.quantity
        if adv > 0:
            micro += 1.0
        elif adv < 0:
            micro -= 1.0

        fair = td.get("_osm_f", micro)
        innov = micro - fair
        err_ema = td.get("_osm_err", abs(innov))
        err_ema += self.OSM_K_SS * (abs(innov) - err_ema)
        td["_osm_err"] = err_ema
        fair += (self.OSM_K_SS / (1.0 + err_ema)) * innov
        td["_osm_f"] = fair

        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        cw = self.OSM_CLEAR_WIDTH
        orders = []
        bv = sv = 0

        skew = round(pos / self.OSM_SKEW_UNIT)
        ask_limit = max(self.OSM_FAIR_STATIC, fair) - max(0, self.OSM_TAKE_WIDTH + skew)
        bid_limit = min(self.OSM_FAIR_STATIC, fair) + max(0, self.OSM_TAKE_WIDTH - skew)
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
        need = self.LIMITS["INTARIAN_PEPPER_ROOT"] - pos
        if need <= 0:
            return []

        tick = timestamp // 100
        mid = (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0
        pep_sum = td.get("_pep_sum", 0.0) + mid - self.PEP_DRIFT * tick
        pep_cnt = td.get("_pep_cnt", 0) + 1
        td["_pep_sum"] = pep_sum
        td["_pep_cnt"] = pep_cnt
        fair = pep_sum / pep_cnt + self.PEP_DRIFT * tick
        fair_int = int(round(fair))

        threshold = fair + self.PEP_ENTRY_TAKE
        orders = []
        for a in sorted(depth.sell_orders):
            if need <= 0 or a > threshold:
                break
            vol = min(-depth.sell_orders[a], need)
            if vol > 0:
                orders.append(Order(symbol, a, vol))
                need -= vol

        if need > 0:
            offset = max(
                self.PEP_BID_FLOOR,
                min(self.PEP_ENTRY_TAKE, max(depth.buy_orders) + 1 - fair_int),
            )
            orders.append(Order(symbol, fair_int + offset, need))
        return orders
