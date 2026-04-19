from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    """v13: v12 + tight TAKE and aggressive SKEW for osmium.

    Changes vs v12 (from batches 1-5 MC sweeps, 500 sessions on both
    rust_simulator and rust_simulator_maf):
      * OSM_TAKE_WIDTH: 3 → 2 (cross the book at 2-tick edge).
      * OSM_SKEW_UNIT: 24 → 12 (skew reacts twice as fast to inventory).

    These two together add +816 access PnL (n=500, ~8 SEM significance;
    95% CIs do not overlap v12). Structural experiments — ladder MAKE,
    inventory-scaled quote size, OFI-gated TAKE, pepper drift-aware
    rebalance overlay — were all flat-to-worse than the tuned params.

    Round 2 strategy:
      * Osmium  — mean-reverting MM around fair=10001 with Kalman micro-price.
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

    # Osmium market-making params (from v5 winner).
    OSM_K_SS = 0.1353
    OSM_FAIR_STATIC = 10001
    OSM_TAKE_WIDTH = 2
    OSM_CLEAR_WIDTH = 2
    OSM_CLEAR_TIGHT_POS = 50
    OSM_VOLUME_LIMIT = 30
    OSM_MAKE_EDGE = 1
    OSM_SKEW_UNIT = 12

    # Pepper buy-and-hold params (from v3/v5 winner).
    PEP_DRIFT = 0.099977
    PEP_ENTRY_TAKE = 5
    PEP_ENTRY_TIMEOUT = 200
    PEP_BID_FLOOR = -6
    PEP_BID_CEIL = 5

    # Risk fail-safes.
    OSM_FAIR_SANITY_MAX = 25   # fair within 25 of mid (~5 std)
    OSM_MAX_SPREAD = 30        # skip MAKE if ask-bid > 30
    PEP_FAIR_SANITY_MAX = 50   # pepper mid drifts, allow wider band
    PEP_MAX_SPREAD = 30

    def bid(self) -> int:
        # MAF: ignored in this MC run; the team decides the auction bid
        # separately based on the observed access uplift (~1400 PnL).
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
            elif symbol == "INTARIAN_PEPPER_ROOT":
                result[symbol] = self._pepper(symbol, depth, pos, state.timestamp, td)
            else:
                result[symbol] = []
            result[symbol] = self._cap_orders(symbol, result[symbol], pos)
        return result, 0, json.dumps(td)

    def _cap_orders(self, symbol, orders, pos):
        lim = self.LIMITS.get(symbol, 0)
        buys = 0
        sells = 0
        kept = []
        for o in orders:
            if o.quantity > 0:
                room = lim - pos - buys
                if room <= 0:
                    continue
                q = min(o.quantity, room)
                kept.append(Order(symbol, o.price, q))
                buys += q
            elif o.quantity < 0:
                room = lim + pos - sells
                if room <= 0:
                    continue
                q = min(-o.quantity, room)
                kept.append(Order(symbol, o.price, -q))
                sells += q
        return kept

    def _kalman_fair(self, depth, td):
        if not depth.buy_orders or not depth.sell_orders:
            return td.get("_osm_f", self.OSM_FAIR_STATIC)
        bb = max(depth.buy_orders)
        ba = min(depth.sell_orders)
        bv = depth.buy_orders[bb]
        av = -depth.sell_orders[ba]
        tot = bv + av
        micro = (bb * av + ba * bv) / tot if tot > 0 else (bb + ba) / 2.0
        f = td.get("_osm_f", micro)
        f += self.OSM_K_SS * (micro - f)
        td["_osm_f"] = f
        return f

    def _osmium(self, d, pos, td):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders)
        ba = min(d.sell_orders)
        mid = (bb + ba) / 2.0
        fair = self._kalman_fair(d, td)

        # Fail-safe: fair/mid divergence
        if abs(fair - mid) > self.OSM_FAIR_SANITY_MAX:
            return []

        take_buy = max(self.OSM_FAIR_STATIC, fair)
        take_sell = min(self.OSM_FAIR_STATIC, fair)

        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        cw = self.OSM_CLEAR_WIDTH - (abs(pos) >= self.OSM_CLEAR_TIGHT_POS)
        orders = []
        bv = sv = 0

        skew = round(pos / self.OSM_SKEW_UNIT)
        tw_ask = max(0, self.OSM_TAKE_WIDTH + skew)
        tw_bid = max(0, self.OSM_TAKE_WIDTH - skew)

        if ba <= take_buy - tw_ask:
            q = min(-d.sell_orders[ba], lim - pos - bv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", ba, q)); bv += q
        if bb >= take_sell + tw_bid:
            q = min(d.buy_orders[bb], lim + pos - sv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", bb, -q)); sv += q

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

        # Fail-safe: wide spread → skip MAKE quotes (TAKE+CLEAR already emitted)
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

        # Fail-safe: fair/mid divergence
        if abs(fair - mid) > self.PEP_FAIR_SANITY_MAX:
            return []

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

        # Fail-safe: skip passive quote if spread is pathological
        if ba - bb > self.PEP_MAX_SPREAD:
            return orders

        if selective and bv < need:
            competing = max(depth.buy_orders)
            offset = max(self.PEP_BID_FLOOR, min(self.PEP_BID_CEIL, competing + 1 - fair_int))
            orders.append(Order(symbol, fair_int + offset, need - bv))

        return orders
