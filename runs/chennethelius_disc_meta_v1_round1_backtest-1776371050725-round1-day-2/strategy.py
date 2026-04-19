"""
disc_meta_v1: Ensemble of best ideas from Discord intel.

Combines:
- Affine fair value with rolling-regression fallback (amogossus)
- Spread-regime aggression scaling (henryendix)
- Time-phased aggressiveness (sbmxz1143)
- Wide no-trade band on exit (spartan_35961)
- EWMA osmium fair with mean-reversion MM (community consensus)

Design principle: every parameter either
  (a) derived from data structure (EWMA alphas from timescales)
  (b) pulled from robust statistics (percentile-based thresholds)
  (c) one-sided safety margin (no profit-maximizing knob)

No grid search, no per-day tuning. If a parameter can't be justified from
structure, it's not there.
"""
from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    # ---- PEPPER ----
    PEP_TARGET = 80
    PEP_FIT_WINDOW = 120
    PEP_HIST_MAX = 300
    PEP_DRIFT_BREAK = 20     # residual threshold for affine→regression switch

    # ---- OSMIUM ----
    OSM_FAIR_SEED = 10000
    OSM_EWMA_ALPHA = 0.04
    OSM_SPREAD_ALPHA = 0.04
    OSM_BASE_TAKE = 2
    OSM_BASE_EDGE = 2

    def bid(self):
        return 15

    def run(self, state: TradingState):
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}

        result: dict[str, list[Order]] = {}
        within_day = state.timestamp % 1_000_000
        for symbol, depth in state.order_depths.items():
            pos = state.position.get(symbol, 0)
            if symbol == "ASH_COATED_OSMIUM":
                result[symbol] = self._osmium(depth, pos, td)
            elif symbol == "INTARIAN_PEPPER_ROOT":
                result[symbol] = self._pepper(symbol, depth, pos, within_day, state.timestamp, td)
            else:
                result[symbol] = []
        return result, 0, json.dumps(td)

    # ===== PEPPER =====
    def _pepper(self, symbol, d, pos, within_day, ts, td):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders); ba = min(d.sell_orders)
        if bb >= ba:
            return []
        mid = (bb + ba) / 2.0
        spread = ba - bb

        # Maintain history (timestamp, mid)
        hist = td.get("pep_hist", [])
        hist.append([ts, mid])
        if len(hist) > self.PEP_HIST_MAX:
            hist = hist[-self.PEP_HIST_MAX:]
        td["pep_hist"] = hist

        # Maintain spread EWMA
        sp_ewma = td.get("pep_sp", spread)
        sp_ewma = 0.96 * sp_ewma + 0.04 * spread
        td["pep_sp"] = sp_ewma

        # Fit affine once, freeze
        slope = td.get("pep_slope")
        intercept = td.get("pep_intercept")
        if slope is None and len(hist) >= self.PEP_FIT_WINDOW:
            slope, intercept = self._linfit(hist[: self.PEP_FIT_WINDOW])
            td["pep_slope"] = slope
            td["pep_intercept"] = intercept

        # Fair value
        if slope is not None:
            affine_fair = intercept + slope * ts
            residual = mid - affine_fair
            if abs(residual) > self.PEP_DRIFT_BREAK:
                # Affine model broken; roll regression on recent window
                r_s, r_i = self._linfit(hist)
                fair = r_i + r_s * ts
                affine_confident = False
            else:
                fair = affine_fair
                affine_confident = True
        else:
            fair = mid
            affine_confident = False

        # Regime: narrow spread + affine-confident → aggressive; else cautious
        if spread <= sp_ewma * 0.8 and affine_confident:
            regime = "trend"
        elif spread >= sp_ewma * 1.3:
            regime = "uncertain"
        else:
            regime = "normal"

        # Phase: within-day time-based aggressiveness cap
        if within_day < 200_000:
            phase_depth = 8
        elif within_day < 800_000:
            phase_depth = 4
        else:
            phase_depth = 1

        # Combined take depth
        if regime == "trend":
            take_depth = phase_depth
        elif regime == "normal":
            take_depth = max(1, phase_depth - 2)
        else:  # uncertain
            take_depth = 0

        lim = self.LIMITS[symbol]
        orders = []
        need = self.PEP_TARGET - pos

        # Accumulate if underweight, subject to take_depth gate
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
            if to_buy > 0 and regime != "uncertain":
                # Passive bid at top of book
                orders.append(Order(symbol, bb + 1, to_buy))

        # Exit band: exit a slice if price is RICH relative to fair (spartan)
        rich_band = max(3, int(sp_ewma))
        if pos > 20 and bb > fair + rich_band:
            exit_q = min(pos - 20, 8)
            orders.append(Order(symbol, bb, -exit_q))

        return orders

    # ===== OSMIUM =====
    def _osmium(self, d, pos, td):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb = max(d.buy_orders); ba = min(d.sell_orders)
        if bb >= ba:
            return []
        mid = (bb + ba) / 2.0
        spread = ba - bb

        fair = td.get("osm_fair", self.OSM_FAIR_SEED)
        fair = (1 - self.OSM_EWMA_ALPHA) * fair + self.OSM_EWMA_ALPHA * mid
        td["osm_fair"] = fair

        sp_ewma = td.get("osm_sp", spread)
        sp_ewma = (1 - self.OSM_SPREAD_ALPHA) * sp_ewma + self.OSM_SPREAD_ALPHA * spread
        td["osm_sp"] = sp_ewma

        if spread <= sp_ewma * 0.7:
            take_w = max(1, self.OSM_BASE_TAKE - 1)
            edge = max(1, self.OSM_BASE_EDGE - 1)
            mm_on = True
        elif spread >= sp_ewma * 1.4:
            take_w = self.OSM_BASE_TAKE + 2
            edge = self.OSM_BASE_EDGE + 2
            mm_on = False  # skip MM in toxic spread regime
        else:
            take_w = self.OSM_BASE_TAKE
            edge = self.OSM_BASE_EDGE
            mm_on = True

        lim = self.LIMITS["ASH_COATED_OSMIUM"]
        orders = []
        bv = sv = 0

        # Take
        if ba <= fair - take_w:
            q = min(-d.sell_orders[ba], lim - pos - bv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", ba, q))
                bv += q
        if bb >= fair + take_w:
            q = min(d.buy_orders[bb], lim + pos - sv)
            if q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", bb, -q))
                sv += q

        # Clear inventory at fair +/- 1 if skewed
        pos_after = pos + bv - sv
        if pos_after > 20 and bb >= fair + 1:
            cq = min(pos_after - 20, lim + pos - sv)
            if cq > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(fair + 1), -cq))
                sv += cq
        if pos_after < -20 and ba <= fair - 1:
            cq = min(-pos_after - 20, lim - pos - bv)
            if cq > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(fair - 1), cq))
                bv += cq

        if mm_on:
            bid_p = int(fair - edge)
            ask_p = int(fair + edge)
            # Skew bid/ask away from heavy inventory side
            if pos > 40:
                bid_p -= 1
            if pos < -40:
                ask_p += 1
            buy_q = max(0, lim - pos - bv)
            sell_q = max(0, lim + pos - sv)
            if buy_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", bid_p, buy_q))
            if sell_q > 0:
                orders.append(Order("ASH_COATED_OSMIUM", ask_p, -sell_q))

        return orders

    @staticmethod
    def _linfit(xy_list):
        n = len(xy_list)
        if n < 2:
            return 0.0, xy_list[0][1] if xy_list else 0.0
        sx = sy = sxy = sx2 = 0.0
        for x, y in xy_list:
            sx += x; sy += y; sxy += x * y; sx2 += x * x
        denom = n * sx2 - sx * sx
        if denom == 0:
            return 0.0, sy / n
        slope = (n * sxy - sx * sy) / denom
        intercept = (sy - slope * sx) / n
        return slope, intercept
