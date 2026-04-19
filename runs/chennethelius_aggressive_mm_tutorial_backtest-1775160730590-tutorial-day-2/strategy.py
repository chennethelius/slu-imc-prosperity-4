from datamodel import Order, OrderDepth, TradingState
class Trader:
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
        fv, lim, orders = 10000, 80, []
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
        bb, ba = max(d.buy_orders), min(d.sell_orders)
        if bb >= ba: return []
        fv, lim, orders = 4990, 80, []
        br, sr = lim-pos, lim+pos
        for a in sorted(d.sell_orders):
            if a < fv and br > 0:
                v = min(-d.sell_orders[a], br); orders.append(Order("TOMATOES",a,v)); br -= v
            else: break
        for b in sorted(d.buy_orders, reverse=True):
            if b > fv and sr > 0:
                v = min(d.buy_orders[b], sr); orders.append(Order("TOMATOES",b,-v)); sr -= v
            else: break
        bp, ap = min(bb+1, 4989), max(ba-1, 4991)
        if br > 0: orders.append(Order("TOMATOES",bp,br))
        if sr > 0: orders.append(Order("TOMATOES",ap,-sr))
        return orders
