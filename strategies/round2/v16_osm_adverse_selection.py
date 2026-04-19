from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    """v16: v15 + Glosten-Milgrom own-trade adverse-selection signal for osmium.

    Change vs v15 (500 sessions MC, seed 20260418):

        When our SELL fills on osmium, an aggressive buyer just hit our
        ask — they revealed bullish information (they pay to cross the
        spread, so they expect upward movement). Symmetrically, our BUY
        fills reveal bearish information.

        The Glosten-Milgrom model says a market maker must update fair
        in the direction of the aggressor's side BEFORE the next tick,
        or suffer adverse selection losses on subsequent quotes.

        Implementation: sum signed own-trade quantity (+qty on our sells,
        -qty on our buys), shift micro by ±1 tick on sign before feeding
        into the v15 Kalman. The ±1 magnitude is chosen so a single fill
        produces a sub-kalman-K nudge (K_SS * 1.0 ≈ 0.14 tick fair move),
        letting the damping decide how much to believe.

        MC stats (paired same-seed per-session delta vs Test_88/v14):

          access:  mean=+145.6 (std 383, t=+8.51, 66.2% session wins)
          noaccess: mean=+181.9 (std 358, t=+11.37, 72.6% session wins)

        The unpaired SEM (≈106 on access) makes this look sub-2-SEM at
        first glance. The PAIRED test strips out market-shock variance
        (same seed, same FV path, same market-trade timing — only
        strategy actions differ). Paired t=+8.51 is decisively significant
        (p < 1e-15). The absolute improvement is small because 66% of the
        time we'd have won anyway; the signal flips 66% of coin-flips
        toward our side rather than adding huge edge per-tick.

        Rollback: deleting the `adv` block reverts to v15 exactly.

    Dead-ends ruled out (tested and rejected — documented here so this
    doesn't get re-explored):

      - Market-trade aggressor flow (bot-to-bot trades) instead of
        own-trades: +138.3 Dacc alone, sparse signal in simulator.
        Stacking with own-trade: byte-identical (no additional info).

      - Top-of-book depth derivative as extra channel: +149.2 Dacc
        combined, pairwise t=+0.79 vs v16 — NOT statistically distinct.

      - OFI (Cont/Kukanov order flow imbalance): pairwise t=-0.06 vs
        v16, zero signal after own-trade already captures flow.

      - Magnitude-scaled adverse (adv/5 clamp ±3) instead of sign ±1:
        pairwise t=+0.09 vs v16, no improvement.

      - Widening MAKE edge by err_ema: -2,687 PnL/sess (always triggers).

      - Threshold-gated MAKE volume reduction on adv>10: signal too
        sparse, byte-identical.

    Architecture and risk fail-safes unchanged from v14/v15:

      1. FAIR_SANITY_MAX — halt if |fair - mid| > 25 (protects against
         stale traderData or feed glitches at init).

      2. MAX_SPREAD — skip MAKE quotes if spread > 30 (keeps us from
         seeding a crossed book into a broken market; TAKE still fires).

      3. POSITION_SAFETY (_cap_orders) — final belt-and-suspenders that
         aggregates buy/sell quantities per symbol and drops tail orders
         that would breach the 80-contract limit. Prevents the exchange
         rule where a single limit-breaching order rejects ALL orders
         for the symbol in that tick.

    Round 2 strategy unchanged at the contract level:
      * Osmium  — mean-reverting MM around fair=10001 with adaptive-K
                  Kalman microprice + Glosten-Milgrom adverse-selection.
      * Pepper  — buy-and-hold capturing the +0.099977/tick ARIMA drift.
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
            own = state.own_trades.get(symbol, [])
            if symbol == "ASH_COATED_OSMIUM":
                result[symbol] = self._osmium(depth, pos, td, own)
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

    def _osmium(self, d, pos, td, own_trades):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders)
        ba = min(d.sell_orders)
        mid = (bb + ba) / 2.0
        bv_tob = d.buy_orders[bb]
        av_tob = -d.sell_orders[ba]
        tot = bv_tob + av_tob
        micro = (bb * av_tob + ba * bv_tob) / tot if tot > 0 else mid

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
