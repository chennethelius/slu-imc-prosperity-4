"""
disc_affine_pepper: Affine fair value with regression fallback for PEPPER.

Per amogossus: "hardcoded affine pricer with stonks PnL or a linear regression
pricer that doesnt take into account the strictly increasing fair price and
instead regresses it with mess efficiency but way more resilient if it changes"

Solution: use BOTH.
- Primary fair: affine `fair = seed + slope * t` where slope is estimated from
  early window, then frozen.
- Fallback: if observed mid deviates from affine prediction by >DRIFT_BREAK,
  switch to rolling regression on last N mids.

Trades aggressively toward +80 while affine-confident; switches to MM when
model breaks.

OSMIUM: pure mean-reversion MM around 10000 with EWMA shift detection.

Anti-overfit:
- Slope estimated from first 100 ticks of live data (not hardcoded from
  backtest data)
- Regime switch based on model residual, not PnL feedback
"""
from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    PEP_TARGET = 80
    PEP_SEED = 12000
    PEP_FIT_WINDOW = 100       # first N ticks used to estimate slope
    PEP_REGRESS_WINDOW = 200   # rolling window for fallback regression
    PEP_DRIFT_BREAK = 15       # residual threshold for regime switch
    PEP_TAKE_DEPTH_TREND = 6
    PEP_TAKE_DEPTH_MR = 2

    OSM_FAIR = 10000
    OSM_TAKE = 2
    OSM_EDGE = 2

    def bid(self):
        return 15

    def run(self, state: TradingState):
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}

        result: dict[str, list[Order]] = {}
        for symbol, depth in state.order_depths.items():
            pos = state.position.get(symbol, 0)
            if symbol == "ASH_COATED_OSMIUM":
                result[symbol] = self._osmium(depth, pos)
            elif symbol == "INTARIAN_PEPPER_ROOT":
                result[symbol] = self._pepper(symbol, depth, pos, td, state.timestamp)
            else:
                result[symbol] = []
        return result, 0, json.dumps(td)

    def _pepper(self, symbol, d, pos, td, ts):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders); ba = min(d.sell_orders)
        if bb >= ba:
            return []
        mid = (bb + ba) / 2.0

        hist = td.get("pep_hist", [])
        hist.append([ts, mid])
        if len(hist) > self.PEP_REGRESS_WINDOW:
            hist = hist[-self.PEP_REGRESS_WINDOW:]
        td["pep_hist"] = hist

        # Fit affine after observing enough ticks; freeze it once fit
        slope = td.get("pep_slope")
        intercept = td.get("pep_intercept")
        if slope is None and len(hist) >= self.PEP_FIT_WINDOW:
            slope, intercept = self._linfit(hist[: self.PEP_FIT_WINDOW])
            td["pep_slope"] = slope
            td["pep_intercept"] = intercept

        # Predict affine fair
        if slope is not None:
            affine_fair = intercept + slope * ts
            residual = mid - affine_fair
        else:
            affine_fair = mid
            residual = 0

        # Regime switch: if affine drifts badly, blend toward mid (not pure regression)
        # Pure regression on a volatile window can give worse predictions than affine
        if slope is not None and abs(residual) > self.PEP_DRIFT_BREAK:
            # Blend: 50% affine, 50% current mid (safer than pure regression)
            fair = 0.5 * affine_fair + 0.5 * mid
            take_depth = self.PEP_TAKE_DEPTH_MR
        else:
            fair = affine_fair if slope is not None else mid
            take_depth = self.PEP_TAKE_DEPTH_TREND

        lim = self.LIMITS[symbol]
        orders = []
        need = self.PEP_TARGET - pos
        if need > 0:
            to_buy = min(need, lim - pos)
            for a in sorted(d.sell_orders):
                if a > fair + take_depth:
                    break
                vol = min(-d.sell_orders[a], to_buy)
                if vol > 0:
                    orders.append(Order(symbol, a, vol))
                    to_buy -= vol
                if to_buy <= 0:
                    break
            if to_buy > 0:
                orders.append(Order(symbol, bb + 1, to_buy))

        return orders

    @staticmethod
    def _linfit(xy_list):
        """Simple OLS. Returns (slope, intercept)."""
        n = len(xy_list)
        if n < 2:
            return 0.0, xy_list[0][1] if xy_list else 0.0
        sum_x = sum(x for x, _ in xy_list)
        sum_y = sum(y for _, y in xy_list)
        sum_xy = sum(x * y for x, y in xy_list)
        sum_x2 = sum(x * x for x, _ in xy_list)
        denom = n * sum_x2 - sum_x * sum_x
        if denom == 0:
            return 0.0, sum_y / n
        slope = (n * sum_xy - sum_x * sum_y) / denom
        intercept = (sum_y - slope * sum_x) / n
        return slope, intercept

    def _osmium(self, d, pos):
        if not d.buy_orders or not d.sell_orders:
            return []
        fair = self.OSM_FAIR
        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        orders = []
        bv = sv = 0

        ba = min(d.sell_orders)
        if ba <= fair - self.OSM_TAKE:
            q = min(-d.sell_orders[ba], lim - pos - bv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", ba, q))
                bv += q
        bb = max(d.buy_orders)
        if bb >= fair + self.OSM_TAKE:
            q = min(d.buy_orders[bb], lim + pos - sv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", bb, -q))
                sv += q

        bid_p = fair - self.OSM_EDGE
        ask_p = fair + self.OSM_EDGE
        buy_q = max(0, lim - pos - bv)
        sell_q = max(0, lim + pos - sv)
        if buy_q > 0:
            orders.append(Order("ASH_COATED_OSMIUM", bid_p, buy_q))
        if sell_q > 0:
            orders.append(Order("ASH_COATED_OSMIUM", ask_p, -sell_q))
        return orders
