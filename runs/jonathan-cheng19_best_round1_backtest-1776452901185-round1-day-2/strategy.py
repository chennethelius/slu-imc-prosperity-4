from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    """
    Test_40 — Test_39 + micro-price Kalman observation.

    Test_39 uses plain mid (bb+ba)/2 as the Kalman observation. Micro-
    price is strictly better: it weights each side's price by the
    OPPOSITE side's volume, capturing book-imbalance pressure. When
    ask has small size vs bid, price will move up (easier to sweep
    ask), so micro-price > mid and Kalman tracks it faster.

      micro = (bb * ask_vol + ba * bid_vol) / (bid_vol + ask_vol)

    This is a PhD-standard microstructure observation (Gatheral,
    Avellaneda, Lehalle: the "weighted midquote" that dominates plain
    midquote as a predictor of next tick's fair).

    Using top-of-book quantities only (best bid/ask levels) rather than
    full-book aggregation keeps the signal tight and responsive.

    Everything else unchanged from Test_39.
    """

    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    OSM_LATENT_VAR = 0.141
    OSM_OBS_VAR = 6.656
    OSM_FAIR_STATIC = 10000
    OSM_TAKE_WIDTH = 3
    OSM_CLEAR_WIDTH = 2
    OSM_CLEAR_WIDTH_TIGHT = 1
    OSM_CLEAR_TIGHT_POS = 50
    OSM_VOLUME_LIMIT = 30
    OSM_MAKE_EDGE = 2
    OSM_MIN_EDGE = 1
    OSM_SKEW_UNIT = 24
    OSM_MIN_TW = 0

    PEP_TARGET = 80
    PEP_DRIFT = 0.100188
    PEP_CALIB_TICKS = 10
    PEP_ENTRY_TAKE = 7
    PEP_ENTRY_TIMEOUT = 200
    PEP_BID_FLOOR = -6
    PEP_BID_CEIL = 5

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
        return result, 0, json.dumps(td)

    def _kalman_fair(self, depth, td):
        if not depth.buy_orders or not depth.sell_orders:
            return td.get("_osm_kf_fair", self.OSM_FAIR_STATIC)
        bb = max(depth.buy_orders)
        ba = min(depth.sell_orders)
        bv = depth.buy_orders[bb]
        av = -depth.sell_orders[ba]
        tot = bv + av
        if tot > 0:
            mid = (bb * av + ba * bv) / tot
        else:
            mid = (bb + ba) / 2.0
        kf = td.get("_osm_kf")
        if kf is None:
            kf = {"fair": mid, "var": self.OSM_OBS_VAR}
        else:
            pred_var = kf["var"] + self.OSM_LATENT_VAR
            K = pred_var / (pred_var + self.OSM_OBS_VAR)
            kf["fair"] = kf["fair"] + K * (mid - kf["fair"])
            kf["var"] = (1 - K) * pred_var
        td["_osm_kf"] = kf
        td["_osm_kf_fair"] = kf["fair"]
        return kf["fair"]

    def _osmium(self, d, pos, td):
        if not d.buy_orders or not d.sell_orders:
            return []
        make_fair = self._kalman_fair(d, td)
        take_fair_buy = max(self.OSM_FAIR_STATIC, make_fair)
        take_fair_sell = min(self.OSM_FAIR_STATIC, make_fair)

        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        cw = self.OSM_CLEAR_WIDTH_TIGHT if abs(pos) >= self.OSM_CLEAR_TIGHT_POS else self.OSM_CLEAR_WIDTH
        vol_lim = self.OSM_VOLUME_LIMIT
        orders = []
        bv = sv = 0

        skew = round(pos / self.OSM_SKEW_UNIT)
        tw_ask = max(self.OSM_MIN_TW, self.OSM_TAKE_WIDTH + skew)
        tw_bid = max(self.OSM_MIN_TW, self.OSM_TAKE_WIDTH - skew)

        ba = min(d.sell_orders); ba_amt = -d.sell_orders[ba]
        if ba <= take_fair_buy - tw_ask:
            q = min(ba_amt, lim - pos - bv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", ba, q)); bv += q
        bb = max(d.buy_orders); bb_amt = d.buy_orders[bb]
        if bb >= take_fair_sell + tw_bid:
            q = min(bb_amt, lim + pos - sv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", bb, -q)); sv += q

        pos_after = pos + bv - sv
        f_bid = int(round(make_fair - cw))
        f_ask = int(round(make_fair + cw))
        if pos_after > 0:
            cq = sum(v for p, v in d.buy_orders.items() if p >= f_ask)
            cq = min(cq, pos_after)
            sent = min(lim + pos - sv, cq)
            if sent > 0:
                orders.append(Order("ASH_COATED_OSMIUM", f_ask, -sent)); sv += sent
        if pos_after < 0:
            cq = sum(-v for p, v in d.sell_orders.items() if p <= f_bid)
            cq = min(cq, -pos_after)
            sent = min(lim - pos - bv, cq)
            if sent > 0:
                orders.append(Order("ASH_COATED_OSMIUM", f_bid, sent)); bv += sent

        bid_edge = max(self.OSM_MIN_EDGE, self.OSM_MAKE_EDGE + skew)
        ask_edge = max(self.OSM_MIN_EDGE, self.OSM_MAKE_EDGE - skew)
        outer_asks = [p for p in d.sell_orders if p > make_fair + ask_edge - 1]
        outer_bids = [p for p in d.buy_orders if p < make_fair - bid_edge + 1]
        if outer_asks and outer_bids:
            baaf = min(outer_asks); bbbf = max(outer_bids)
            if baaf <= make_fair + ask_edge and pos <= vol_lim:
                baaf = int(round(make_fair + ask_edge + 1))
            if bbbf >= make_fair - bid_edge and pos >= -vol_lim:
                bbbf = int(round(make_fair - bid_edge - 1))
            bid = bbbf + 1; ask = baaf - 1
            buy_q = lim - pos - bv
            if buy_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(bid), buy_q))
            sell_q = lim + pos - sv
            if sell_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(ask), -sell_q))
        return orders

    def _pepper(self, symbol, depth, pos, timestamp, td):
        if not depth.buy_orders or not depth.sell_orders:
            return []
        lim = self.LIMITS["INTARIAN_PEPPER_ROOT"]
        tick = timestamp // 100

        bb = max(depth.buy_orders)
        ba = min(depth.sell_orders)
        mid = (bb + ba) / 2.0

        samples = td.get("_pep_samples", [])
        if tick < self.PEP_CALIB_TICKS:
            samples.append(mid - self.PEP_DRIFT * tick)
            td["_pep_samples"] = samples

        intercept = sum(samples) / len(samples) if samples else mid
        fair = intercept + self.PEP_DRIFT * tick

        need = self.PEP_TARGET - pos
        if need <= 0:
            return []

        to_buy = min(need, lim - pos)
        bv = 0
        orders = []
        selective = tick < self.PEP_ENTRY_TIMEOUT

        if selective:
            for a in sorted(depth.sell_orders):
                if bv >= to_buy:
                    break
                if a <= fair + self.PEP_ENTRY_TAKE:
                    vol = min(-depth.sell_orders[a], to_buy - bv)
                    if vol > 0:
                        orders.append(Order(symbol, a, vol))
                        bv += vol
            remaining = to_buy - bv
            if remaining > 0:
                competing = max(depth.buy_orders)
                target = competing + 1 - int(round(fair))
                target = max(self.PEP_BID_FLOOR, min(self.PEP_BID_CEIL, target))
                bid_price = int(round(fair)) + target
                orders.append(Order(symbol, bid_price, remaining))
        else:
            for a in sorted(depth.sell_orders):
                if bv >= to_buy:
                    break
                vol = min(-depth.sell_orders[a], to_buy - bv)
                if vol > 0:
                    orders.append(Order(symbol, a, vol))
                    bv += vol

        return orders
