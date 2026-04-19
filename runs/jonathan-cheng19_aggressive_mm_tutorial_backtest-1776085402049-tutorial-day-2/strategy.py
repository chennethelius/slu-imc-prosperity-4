from datamodel import Order, OrderDepth, TradingState
class Trader:
    """
    Maximally aggressive taker on TOMATOES.
    - Bot 2 FV inversion for precise fair value (±0.25)
    - Takes at and through FV with zero inventory penalty
    - Passive penny-jump at FV for remaining capacity
    """
    def run(self, state: TradingState):
        result = {}
        for sym, depth in state.order_depths.items():
            if sym == "EMERALDS":
                result[sym] = self._em(depth, state.position.get(sym,0))
            elif sym == "TOMATOES":
                result[sym] = self._tom(depth, state.position.get(sym,0))
            else:
                result[sym] = []
        return result, 0, ""
    def _em(self, d, pos):
        if not d.buy_orders or not d.sell_orders: return []
        bb, ba = max(d.buy_orders), min(d.sell_orders)
        if bb >= ba: return []
        fv, lim = 10000, 80
        orders = []
        br, sr = lim-pos, lim+pos
        for a in sorted(d.sell_orders):
            if a < fv and br > 0:
                v = min(-d.sell_orders[a], br); orders.append(Order("EMERALDS",a,v)); br -= v
            else: break
        for b in sorted(d.buy_orders, reverse=True):
            if b > fv and sr > 0:
                v = min(d.buy_orders[b], sr); orders.append(Order("EMERALDS",b,-v)); sr -= v
            else: break
        bp, ap = min(bb+1, fv-1), max(ba-1, fv+1)
        if br > 0: orders.append(Order("EMERALDS",bp,br))
        if sr > 0: orders.append(Order("EMERALDS",ap,-sr))
        return orders
    def _tom(self, d, pos):
        if not d.buy_orders or not d.sell_orders: return []
        bids = sorted(d.buy_orders.keys(), reverse=True)
        asks = sorted(d.sell_orders.keys())
        bb, ba = bids[0], asks[0]
        if bb >= ba: return []
        lim = 80

        # --- Bot 2 FV inversion (±0.25 precision) ---
        # Bot 2: bid = floor(FV+0.75)-7, ask = ceil(FV+0.25)+6
        # Skip Bot 3 levels when 3+ on a side (Bot 3 is near-FV, rare)
        bot2_bid = bids[1] if len(bids) >= 3 else bids[0]
        bot2_ask = asks[1] if len(asks) >= 3 else asks[0]
        fv_low = max(bot2_bid + 6.25, bot2_ask - 7.25)
        fv_high = min(bot2_bid + 7.25, bot2_ask - 6.25)

        # Fallback to wall-mid if Bot 2 inversion inconsistent
        bid_wall = min(d.buy_orders.keys())
        ask_wall = max(d.sell_orders.keys())
        wall_mid = (bid_wall + ask_wall) / 2.0

        if fv_low <= fv_high and abs((fv_low + fv_high) / 2 - wall_mid) < 1.5:
            fv = (fv_low + fv_high) / 2.0
        else:
            fv = wall_mid

        fvi = int(round(fv))
        orders = []
        br, sr = lim - pos, lim + pos

        # AGGRESSIVE TAKE: take at FV and better — no inventory penalty
        for a in sorted(d.sell_orders):
            if a <= fv and br > 0:
                v = min(-d.sell_orders[a], br)
                orders.append(Order("TOMATOES", a, v)); br -= v
            else:
                break
        for b in sorted(d.buy_orders, reverse=True):
            if b >= fv and sr > 0:
                v = min(d.buy_orders[b], sr)
                orders.append(Order("TOMATOES", b, -v)); sr -= v
            else:
                break

        # Passive: penny-jump right at FV boundary
        bp = min(bb + 1, fvi)
        ap = max(ba - 1, fvi)
        # Avoid self-trade
        if bp >= ap:
            bp = fvi - 1
            ap = fvi + 1
        if br > 0:
            orders.append(Order("TOMATOES", bp, br))
        if sr > 0:
            orders.append(Order("TOMATOES", ap, -sr))
        return orders
