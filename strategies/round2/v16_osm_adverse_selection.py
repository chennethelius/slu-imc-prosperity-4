from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    """v16 (lean + aggressive-fill pepper).

    Osmium: Glosten-Milgrom own-trade adverse-selection + adaptive-K
    Kalman micro. When our SELL fills, an aggressive buyer hit our ask
    — bullish info. Symmetric for own BUYs. Shift micro by +/-1 tick
    on sign before the Kalman, so a single fill produces a ~0.14-tick
    fair nudge that the |innov| damping discounts in noisy regimes.

    Pepper: buy-and-hold, fill to limit as fast as possible. Pepper
    has a +0.099977/tick ARIMA drift — every tick earlier we reach
    the 80-unit cap is +8 SS of captured drift, so we take every ask
    unconditionally and quote the tightest non-crossing passive bid
    (best_ask - 1) to absorb any residual need instantly.

    Dead-ends ruled out (pairwise vs prior v16, 500 paired sessions):
      - Book-pressure derivative:    t=+0.79
      - OFI (Cont/Kukanov):          t=-0.06
      - Adverse EMA persistence:     t=-13.85
      - Confidence-damped adv:       t=-1.44
      - Avellaneda-Stoikov widen:    -19 SEM
      - Bayesian fair shrinkage:     t=-29 (catastrophic)
    """

    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    OSM_K_SS = 0.1353
    OSM_FAIR_STATIC = 10001
    OSM_TAKE_WIDTH = 2
    OSM_CLEAR_WIDTH = 2
    OSM_VOLUME_LIMIT = 30
    OSM_MAKE_EDGE = 1
    OSM_SKEW_UNIT = 12

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
                    symbol, depth, state.position.get(symbol, 0)
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

    def _pepper(self, symbol, depth, pos):
        if not depth.buy_orders or not depth.sell_orders:
            return []
        need = self.LIMITS["INTARIAN_PEPPER_ROOT"] - pos
        if need <= 0:
            return []

        orders = []
        for a in sorted(depth.sell_orders):
            if need <= 0:
                break
            vol = min(-depth.sell_orders[a], need)
            if vol > 0:
                orders.append(Order(symbol, a, vol))
                need -= vol

        if need > 0:
            orders.append(Order(symbol, min(depth.sell_orders) - 1, need))
        return orders
