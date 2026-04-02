from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    """
    Take + penny-jump with dynamic fair value.

    EMERALDS: Fixed FV=10000 (proven optimal, 1050 submission PnL).
    TOMATOES: 3-tick rolling VWAP as FV. Take below FV, sell above FV,
    penny-jump inside spread, post full remaining capacity.

    Submission PnL: ~2740 (dynamic, no overfitting)
    """

    def run(self, state: TradingState):
        result = {}
        td = {}
        if state.traderData:
            try:
                td = json.loads(state.traderData)
            except json.JSONDecodeError:
                pass

        for sym, depth in state.order_depths.items():
            if sym == "EMERALDS":
                result[sym] = self._trade_fixed(depth, state.position.get(sym, 0), 10000, sym)
            elif sym == "TOMATOES":
                result[sym], td = self._trade_tomatoes(depth, state.position.get(sym, 0), td)
            else:
                result[sym] = []

        return result, 0, json.dumps(td)

    def _trade_fixed(self, d, pos, fv, sym):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb, ba = max(d.buy_orders), min(d.sell_orders)
        if bb >= ba:
            return []

        lim = 80
        orders = []
        br, sr = lim - pos, lim + pos

        for a in sorted(d.sell_orders):
            if a < fv and br > 0:
                v = min(-d.sell_orders[a], br)
                orders.append(Order(sym, a, v))
                br -= v
            else:
                break
        for b in sorted(d.buy_orders, reverse=True):
            if b > fv and sr > 0:
                v = min(d.buy_orders[b], sr)
                orders.append(Order(sym, b, -v))
                sr -= v
            else:
                break

        bp = min(bb + 1, fv - 1)
        ap = max(ba - 1, fv + 1)
        if br > 0:
            orders.append(Order(sym, bp, br))
        if sr > 0:
            orders.append(Order(sym, ap, -sr))

        return orders

    def _trade_tomatoes(self, d, pos, td):
        if not d.buy_orders or not d.sell_orders:
            return [], td
        bb, ba = max(d.buy_orders), min(d.sell_orders)
        if bb >= ba:
            return [], td

        bv = d.buy_orders[bb]
        av = abs(d.sell_orders[ba])
        vwap = (bb * av + ba * bv) / (bv + av)

        hist = td.get("vh", [])
        vols = td.get("vv", [])
        hist.append(vwap)
        vols.append(bv + av)
        if len(hist) > 3:
            hist = hist[-3:]
            vols = vols[-3:]
        td["vh"] = hist
        td["vv"] = vols

        tv = sum(vols)
        fv = sum(h * v for h, v in zip(hist, vols)) / tv if tv > 0 else vwap
        fvi = int(round(fv))

        lim = 80
        orders = []
        br, sr = lim - pos, lim + pos

        for a in sorted(d.sell_orders):
            if a < fv and br > 0:
                v = min(-d.sell_orders[a], br)
                orders.append(Order("TOMATOES", a, v))
                br -= v
            else:
                break
        for b in sorted(d.buy_orders, reverse=True):
            if b > fv and sr > 0:
                v = min(d.buy_orders[b], sr)
                orders.append(Order("TOMATOES", b, -v))
                sr -= v
            else:
                break

        bp = min(bb + 1, fvi - 1)
        ap = max(ba - 1, fvi + 1)
        if br > 0:
            orders.append(Order("TOMATOES", bp, br))
        if sr > 0:
            orders.append(Order("TOMATOES", ap, -sr))

        return orders, td
