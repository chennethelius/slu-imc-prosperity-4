import json
from datamodel import Order, OrderDepth, TradingState
class Trader:
    """
    Maximally aggressive taker on TOMATOES with AR(1) mean-reversion prediction.
    phi = -0.18: returns are mean-reverting, so predicted next FV = fv + phi*(fv - prev_fv).
    """
    PHI = -0.18
    def run(self, state: TradingState):
        result = {}
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}
        prev_fv_tom = td.get('pfv_tom', None)
        new_td = dict(td)
        tom_trades = state.market_trades.get('TOMATOES', []) + state.own_trades.get('TOMATOES', [])
        for sym, depth in state.order_depths.items():
            if sym == "EMERALDS":
                result[sym] = self._em(depth, state.position.get(sym,0))
            elif sym == "TOMATOES":
                orders, fv = self._tom(depth, state.position.get(sym,0), prev_fv_tom, tom_trades)
                result[sym] = orders
                new_td['pfv_tom'] = fv
            else:
                result[sym] = []
        return result, 0, json.dumps(new_td)
    def _em(self, d, pos):
        if not d.buy_orders or not d.sell_orders: return []
        bb, ba = max(d.buy_orders), min(d.sell_orders)
        if bb >= ba: return []
        fv, lim = 10000, 80
        # Inventory-shifted FV (from optimized_mm Variant L): shifts take/quote threshold by pos
        fv_eff = fv - pos * 0.15
        fvi = int(round(fv_eff))
        orders = []
        br, sr = lim-pos, lim+pos
        for a in sorted(d.sell_orders):
            if a < fv_eff and br > 0:
                v = min(-d.sell_orders[a], br); orders.append(Order("EMERALDS",a,v)); br -= v
            else: break
        for b in sorted(d.buy_orders, reverse=True):
            if b > fv_eff and sr > 0:
                v = min(d.buy_orders[b], sr); orders.append(Order("EMERALDS",b,-v)); sr -= v
            else: break
        bp, ap = min(bb+1, fvi-1), max(ba-1, fvi+1)
        if br > 0: orders.append(Order("EMERALDS",bp,br))
        if sr > 0: orders.append(Order("EMERALDS",ap,-sr))
        return orders
    def _tom(self, d, pos, prev_fv, recent_trades):
        if not d.buy_orders or not d.sell_orders: return [], None
        bids = sorted(d.buy_orders.keys(), reverse=True)
        asks = sorted(d.sell_orders.keys())
        bb, ba = bids[0], asks[0]
        if bb >= ba: return [], None
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

        # AR(1) mean-reversion: predicted next FV = fv + phi * (fv - prev_fv)
        fv_pred = fv
        if prev_fv is not None:
            fv_pred = fv + self.PHI * (fv - prev_fv)

        fvi = int(round(fv_pred))
        orders = []
        br, sr = lim - pos, lim + pos
        # P3-style: reserve 20 units of capacity for passive fills (don't burn all on takes)
        soft_lim = 60
        take_br = max(0, soft_lim - pos)
        take_sr = max(0, soft_lim + pos)

        # Asymmetric take: stricter buy (less long-building), looser sell
        for a in sorted(d.sell_orders):
            if a < fv_pred and take_br > 0 and br > 0:
                v = min(-d.sell_orders[a], take_br, br)
                orders.append(Order("TOMATOES", a, v)); br -= v; take_br -= v
            else:
                break
        for b in sorted(d.buy_orders, reverse=True):
            if b >= fv_pred - 0.5 and take_sr > 0 and sr > 0:
                v = min(d.buy_orders[b], take_sr, sr)
                orders.append(Order("TOMATOES", b, -v)); sr -= v; take_sr -= v
            else:
                break

        # Sylvain/YBansal-inspired: penny inside nearest level >1 tick from fair
        # avoids pennying Bot 3 noise right next to FV (adverse selection)
        asks_above = [p for p in d.sell_orders if p > fv_pred + 1]
        bids_below = [p for p in d.buy_orders if p < fv_pred - 1]
        baaf = min(asks_above) if asks_above else fvi + 2
        bbbf = max(bids_below) if bids_below else fvi - 2
        bp = bbbf + 1
        ap = baaf - 1
        if bp >= ap:
            bp = fvi - 1
            ap = fvi + 1

        if br > 0:
            orders.append(Order("TOMATOES", bp, br))
        if sr > 0:
            orders.append(Order("TOMATOES", ap, -sr))
        return orders, fv
