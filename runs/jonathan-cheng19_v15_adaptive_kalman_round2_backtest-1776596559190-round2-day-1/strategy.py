from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    """v15: v14 + adaptive Kalman gain for osmium fair.

    Change vs v14 (500 sessions MC, seed 20260418):

        Fair is a Kalman-ish filter: fair += K * (micro - fair). Before
        this patch, K=0.1353 was constant. Kalman theory says K should
        shrink when the innovation (micro - fair) has high variance —
        noisy micro updates should pull fair less.

        New: track EMA of |micro - fair| reusing K_SS as its own update
        rate (no new parameter). Effective K = K_SS / (1 + err_ema).
        When micro is near fair (err_ema ~ 0), K_eff ~= K_SS (normal).
        When micro spikes (err_ema=5), K_eff ~= 0.023 (heavy damping,
        trust prior fair).

        MC delta: +137.5 access / +182.7 noaccess vs v14. This is 1.26
        SEM, BELOW the 2-SEM significance threshold (~212 on access
        with Test_88 stdev=2374). Expected-value positive but not
        statistically rigorous at 500 sessions. Included in case the
        real-environment behavior differs from simulator — Kalman
        damping is principled even if the simulator's signal-to-noise
        doesn't reward it strongly.

    Rollback: setting k_eff = self.OSM_K_SS (drop the division) reverts
    to v14 exactly.

    Round 2 strategy unchanged at the contract level:
      * Osmium  — mean-reverting MM around fair=10001 with adaptive-K Kalman micro-price.
      * Pepper  — buy-and-hold capturing the +0.099977/tick ARIMA drift.

    Risk fail-safes (all three simple and cheap):

      1. FAIR_SANITY_MAX: if |fair - mid| exceeds a sane band, stop acting for
         this tick. Protects against bad running-mean initialisation, a stale
         traderData blob, or an upstream fair/mid divergence after a jump.

      2. MAX_SPREAD: if best_ask - best_bid is unusually wide (likely a thin
         book or a feed glitch), skip MAKE quotes but still TAKE mispriced
         orders. Avoids seeding a crossed/locked book into a broken market.

      3. POSITION_SAFETY: final belt-and-suspenders check that the sum of
         buy/sell quantities per symbol stays within the 80-contract limit.
         The exchange rejects ALL orders in the tick if any order would breach
         — this check drops orders from the tail instead of losing the whole
         tick. Defends against edge-case rounding in the take/clear logic.
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

    OSM_FAIR_SANITY_MAX = 25
    OSM_MAX_SPREAD = 30
    PEP_FAIR_SANITY_MAX = 50
    PEP_MAX_SPREAD = 30

    def bid(self) -> int:
        return 0

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
            else:
                result[symbol] = self._pepper(symbol, depth, pos, state.timestamp, td)
            result[symbol] = self._cap_orders(symbol, result[symbol], pos)
        return result, 0, json.dumps(td)

    def _cap_orders(self, symbol, orders, pos):
        lim = self.LIMITS.get(symbol, 0)
        buys = sells = 0
        kept = []
        for o in orders:
            if o.quantity > 0:
                q = min(o.quantity, lim - pos - buys)
                if q > 0:
                    kept.append(Order(symbol, o.price, q))
                    buys += q
            elif o.quantity < 0:
                q = min(-o.quantity, lim + pos - sells)
                if q > 0:
                    kept.append(Order(symbol, o.price, -q))
                    sells += q
        return kept

    def _osmium(self, d, pos, td):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders)
        ba = min(d.sell_orders)
        mid = (bb + ba) / 2.0
        bv_tob = d.buy_orders[bb]
        av_tob = -d.sell_orders[ba]
        tot = bv_tob + av_tob
        micro = (bb * av_tob + ba * bv_tob) / tot if tot > 0 else mid
        fair = td.get("_osm_f", micro)
        innov = micro - fair
        err = abs(innov)
        err_ema = td.get("_osm_err", err)
        err_ema += self.OSM_K_SS * (err - err_ema)
        td["_osm_err"] = err_ema
        k_eff = self.OSM_K_SS / (1.0 + err_ema)
        fair += k_eff * innov
        td["_osm_f"] = fair

        if abs(fair - mid) > self.OSM_FAIR_SANITY_MAX:
            return []

        take_buy = max(self.OSM_FAIR_STATIC, fair)
        take_sell = min(self.OSM_FAIR_STATIC, fair)
        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        cw = self.OSM_CLEAR_WIDTH
        orders = []
        bv = sv = 0

        skew = round(pos / self.OSM_SKEW_UNIT)
        tw_ask = max(0, self.OSM_TAKE_WIDTH + skew)
        tw_bid = max(0, self.OSM_TAKE_WIDTH - skew)

        ask_limit = take_buy - tw_ask
        bid_limit = take_sell + tw_bid
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

        if ba - bb > self.OSM_MAX_SPREAD:
            return orders

        bid_edge = max(1, self.OSM_MAKE_EDGE + skew)
        ask_edge = max(1, self.OSM_MAKE_EDGE - skew)
        ask_gate = fair + ask_edge - 1
        bid_gate = fair - bid_edge + 1
        baaf = min((p for p in d.sell_orders if p > ask_gate), default=None)
        bbbf = max((p for p in d.buy_orders if p < bid_gate), default=None)
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
        mid = (bb + ba) / 2.0

        pep_sum = td.get("_pep_sum", 0.0) + mid - self.PEP_DRIFT * tick
        pep_cnt = td.get("_pep_cnt", 0) + 1
        td["_pep_sum"] = pep_sum
        td["_pep_cnt"] = pep_cnt

        fair = pep_sum / pep_cnt + self.PEP_DRIFT * tick
        fair_int = int(round(fair))

        if abs(fair - mid) > self.PEP_FAIR_SANITY_MAX:
            return []

        need = lim - pos
        if need <= 0:
            return []

        threshold = fair + self.PEP_ENTRY_TAKE

        orders = []
        for a in sorted(depth.sell_orders):
            if need <= 0 or a > threshold:
                break
            vol = min(-depth.sell_orders[a], need)
            if vol > 0:
                orders.append(Order(symbol, a, vol))
                need -= vol

        if ba - bb > self.PEP_MAX_SPREAD:
            return orders

        if need > 0:
            offset = max(self.PEP_BID_FLOOR,
                         min(self.PEP_ENTRY_TAKE, bb + 1 - fair_int))
            orders.append(Order(symbol, fair_int + offset, need))

        return orders
