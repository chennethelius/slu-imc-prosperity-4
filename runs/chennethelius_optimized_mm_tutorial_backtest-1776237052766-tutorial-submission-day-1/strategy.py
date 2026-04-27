from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List


class Trader:
    """
    Variant L: Sub-tick FV from Bot 2 rounding inversion

    Bot 2 uses asymmetric rounding:
      bid = floor(FV + 0.75) - 7
      ask = ceil(FV + 0.25) + 6

    By inverting: FV lies in the intersection of:
      [bid + 6.25, bid + 7.25) from bid formula
      (ask - 7.25, ask - 6.25] from ask formula

    This gives FV to ±0.25 precision vs wall-mid's ±0.5.
    When Bot 3 is present (3+ levels on a side), use second-best level.
    Falls back to wall-mid if Bot 2 estimate seems inconsistent.
    """

    LIMITS = {"EMERALDS": 80, "TOMATOES": 80}

    def run(self, state: TradingState) -> tuple[dict[str, list[Order]], int, str]:
        result: dict[str, list[Order]] = {}
        for symbol, depth in state.order_depths.items():
            if symbol not in self.LIMITS:
                result[symbol] = []
                continue
            pos = state.position.get(symbol, 0)
            if symbol == "EMERALDS":
                result[symbol] = self._emeralds(depth, pos)
            elif symbol == "TOMATOES":
                result[symbol] = self._tomatoes(symbol, depth, pos)
            else:
                result[symbol] = []
        return result, 0, ""

    def _emeralds(self, d, pos):
        if not d.buy_orders or not d.sell_orders:
            return []
        bb, ba = max(d.buy_orders), min(d.sell_orders)
        if bb >= ba:
            return []
        fv, lim = 10000, 80
        fv_eff = fv - pos * 0.15
        fvi = int(round(fv_eff))
        orders = []
        br, sr = lim - pos, lim + pos
        for a in sorted(d.sell_orders):
            if a < fv_eff and br > 0:
                v = min(-d.sell_orders[a], br)
                orders.append(Order("EMERALDS", a, v)); br -= v
            else:
                break
        for b in sorted(d.buy_orders, reverse=True):
            if b > fv_eff and sr > 0:
                v = min(d.buy_orders[b], sr)
                orders.append(Order("EMERALDS", b, -v)); sr -= v
            else:
                break
        bp = min(bb + 1, fvi - 1)
        ap = max(ba - 1, fvi + 1)
        if br > 0:
            orders.append(Order("EMERALDS", bp, br))
        if sr > 0:
            orders.append(Order("EMERALDS", ap, -sr))
        return orders

    def _tomatoes(self, symbol, depth, pos):
        if not depth.buy_orders or not depth.sell_orders:
            return []
        bids = sorted(depth.buy_orders.keys(), reverse=True)
        asks = sorted(depth.sell_orders.keys())
        bb, ba = bids[0], asks[0]
        if bb >= ba:
            return []

        # Identify Bot 2 levels (skip Bot 3 when present)
        # Bot 3 is rare (6.3%), single-sided, places near FV (above Bot 2 bid, below Bot 2 ask)
        # When 3+ levels: second level is Bot 2. When 2 levels: best is Bot 2.
        bot2_bid = bids[1] if len(bids) >= 3 else bids[0]
        bot2_ask = asks[1] if len(asks) >= 3 else asks[0]

        # Invert Bot 2's rounding to get precise FV range
        # bid = floor(FV + 0.75) - 7  =>  FV in [bid + 6.25, bid + 7.25)
        # ask = ceil(FV + 0.25) + 6   =>  FV in (ask - 7.25, ask - 6.25]
        fv_low = max(bot2_bid + 6.25, bot2_ask - 7.25)
        fv_high = min(bot2_bid + 7.25, bot2_ask - 6.25)

        # Sanity check: range should be valid and narrow
        bid_wall = min(depth.buy_orders.keys())
        ask_wall = max(depth.sell_orders.keys())
        wall_mid = (bid_wall + ask_wall) / 2.0

        if fv_low <= fv_high and abs((fv_low + fv_high) / 2 - wall_mid) < 1.5:
            fv = (fv_low + fv_high) / 2.0
        else:
            fv = wall_mid  # fallback

        fv_eff = fv - pos * 0.15
        fvi = int(round(fv_eff))

        lim = 80
        orders = []
        br, sr = lim - pos, lim + pos

        for a in asks:
            if a < fv_eff and br > 0:
                v = min(-depth.sell_orders[a], br)
                orders.append(Order(symbol, a, v)); br -= v
            else:
                break
        for b in bids:
            if b > fv_eff and sr > 0:
                v = min(depth.buy_orders[b], sr)
                orders.append(Order(symbol, b, -v)); sr -= v
            else:
                break

        bp = min(bb + 1, fvi - 1)
        ap = max(ba - 1, fvi + 1)
        if br > 0:
            orders.append(Order(symbol, bp, br))
        if sr > 0:
            orders.append(Order(symbol, ap, -sr))
        return orders
