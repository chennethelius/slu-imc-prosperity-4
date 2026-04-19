"""
r2_imbalance: Order book imbalance signal for OSMIUM direction.

Uses bid/ask volume ratio as a short-term directional predictor:
  imbalance = bid_vol / (bid_vol + ask_vol)  ∈ [0, 1]
  > 0.5 = more bids = buy pressure = price likely to tick up
  < 0.5 = more asks = sell pressure = price likely to tick down

The signal adjusts take aggressiveness:
- High imbalance (buy pressure) → more willing to take asks (buy)
- Low imbalance (sell pressure) → more willing to take bids (sell)
- Neutral → standard symmetric MM

PEPPER: identical to Test_9_e3.
MAF: bid 20.
"""
from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    OSM_FAIR_SEED = 10000
    OSM_EWMA_ALPHA = 0.05
    OSM_IMB_ALPHA = 0.1        # EWMA on imbalance signal
    OSM_TAKE_WIDTH = 3
    OSM_IMB_TAKE_BONUS = 2     # extra take width when signal is strong
    OSM_CLEAR_WIDTH = 2
    OSM_MAKE_EDGE = 3
    OSM_VOL_LIMIT = 30

    PEP_TARGET = 80
    PEP_TAKE_DEPTH = 6

    def bid(self):
        return 20

    def run(self, state: TradingState):
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}

        result: dict[str, list[Order]] = {}
        for symbol, depth in state.order_depths.items():
            pos = state.position.get(symbol, 0)
            if symbol == "ASH_COATED_OSMIUM":
                result[symbol] = self._osmium(depth, pos, td)
            elif symbol == "INTARIAN_PEPPER_ROOT":
                result[symbol] = self._pepper(symbol, depth, pos)
            else:
                result[symbol] = []
        return result, 0, json.dumps(td)

    def _osmium(self, d, pos, td):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders); ba = min(d.sell_orders)
        if bb >= ba:
            return []
        mid = (bb + ba) / 2.0

        # Fair value
        fair = self.OSM_FAIR_SEED

        # Order book imbalance
        total_bid = sum(d.buy_orders.values())
        total_ask = sum(abs(v) for v in d.sell_orders.values())
        raw_imb = total_bid / (total_bid + total_ask) if (total_bid + total_ask) > 0 else 0.5

        imb = td.get("osm_imb", 0.5)
        imb = (1 - self.OSM_IMB_ALPHA) * imb + self.OSM_IMB_ALPHA * raw_imb
        td["osm_imb"] = imb

        # Directional signal: how far from neutral
        # imb > 0.6 = buy pressure, imb < 0.4 = sell pressure
        buy_signal = max(0, imb - 0.5) * 2   # 0 to 1
        sell_signal = max(0, 0.5 - imb) * 2  # 0 to 1

        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        tw = self.OSM_TAKE_WIDTH
        cw = self.OSM_CLEAR_WIDTH
        edge = self.OSM_MAKE_EDGE
        vol_lim = self.OSM_VOL_LIMIT
        bonus = self.OSM_IMB_TAKE_BONUS
        orders = []
        bv = sv = 0

        # Take: widen take on signal side
        buy_tw = tw + int(buy_signal * bonus)   # more aggressive buy when buy pressure
        sell_tw = tw + int(sell_signal * bonus)  # more aggressive sell when sell pressure

        if ba <= fair - buy_tw:
            q = min(-d.sell_orders[ba], lim - pos - bv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", ba, q)); bv += q
        if bb >= fair + sell_tw:
            q = min(d.buy_orders[bb], lim + pos - sv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", bb, -q)); sv += q

        # Clear inventory
        pos_after = pos + bv - sv
        f_bid = int(fair - cw); f_ask = int(fair + cw)
        if pos_after > 0:
            cq = sum(v for p, v in d.buy_orders.items() if p >= f_ask)
            cq = min(cq, pos_after, lim + pos - sv)
            if cq > 0:
                orders.append(Order("ASH_COATED_OSMIUM", f_ask, -cq)); sv += cq
        if pos_after < 0:
            cq = sum(-v for p, v in d.sell_orders.items() if p <= f_bid)
            cq = min(cq, -pos_after, lim - pos - bv)
            if cq > 0:
                orders.append(Order("ASH_COATED_OSMIUM", f_bid, cq)); bv += cq

        # Passive MM
        baaf = [p for p in d.sell_orders if p > fair + edge - 1]
        bbbf = [p for p in d.buy_orders if p < fair - edge + 1]
        if baaf and bbbf:
            baaf_min = min(baaf); bbbf_max = max(bbbf)
            if baaf_min <= fair + edge and pos <= vol_lim:
                baaf_min = fair + edge + 1
            if bbbf_max >= fair - edge and pos >= -vol_lim:
                bbbf_max = fair - edge - 1
            bid_p = bbbf_max + 1; ask_p = baaf_min - 1
            buy_q = max(0, lim - pos - bv)
            sell_q = max(0, lim + pos - sv)
            if buy_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(bid_p), buy_q))
            if sell_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(ask_p), -sell_q))
        return orders

    def _pepper(self, symbol, depth, pos):
        if not depth.buy_orders or not depth.sell_orders:
            return []
        lim = self.LIMITS[symbol]
        bb = max(depth.buy_orders); ba = min(depth.sell_orders)
        if bb >= ba:
            return []
        mid = (bb + ba) / 2.0
        need = self.PEP_TARGET - pos
        if need <= 0:
            return []
        orders = []
        to_buy = min(need, lim - pos)
        for a in sorted(depth.sell_orders):
            if a > mid + self.PEP_TAKE_DEPTH:
                break
            vol = min(-depth.sell_orders[a], to_buy)
            if vol > 0:
                orders.append(Order(symbol, a, vol)); to_buy -= vol
            if to_buy <= 0:
                break
        if to_buy > 0:
            orders.append(Order(symbol, bb + 1, to_buy))
        return orders
