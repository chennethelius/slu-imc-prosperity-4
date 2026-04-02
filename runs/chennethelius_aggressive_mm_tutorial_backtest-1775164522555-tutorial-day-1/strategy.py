from datamodel import Order, OrderDepth, TradingState
class Trader:
    """
    Key idea: use micro-price (volume-weighted mid) as FV.
    Micro-price naturally incorporates book imbalance — when bid volume
    is high, micro-price shifts toward ask, predicting upward move.
    No parameters to overfit.
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
        # Compute FV using ALL visible book levels, weighted by volume
        total_bid_pv = sum(p * d.buy_orders[p] for p in bids)
        total_bid_v = sum(d.buy_orders[p] for p in bids)
        total_ask_pv = sum(p * abs(d.sell_orders[p]) for p in asks)
        total_ask_v = sum(abs(d.sell_orders[p]) for p in asks)
        # Micro-price: weighted by opposite side (bid-weighted ask price, ask-weighted bid price)
        fv = (total_bid_pv / total_bid_v * total_ask_v + total_ask_pv / total_ask_v * total_bid_v) / (total_bid_v + total_ask_v)
        fvi = int(round(fv))
        lim = 80
        orders = []
        br, sr = lim-pos, lim+pos
        # Take below FV
        for a in sorted(d.sell_orders):
            if a < fv and br > 0:
                v = min(-d.sell_orders[a], br); orders.append(Order("TOMATOES",a,v)); br -= v
            else: break
        # Sell above FV
        for b in sorted(d.buy_orders, reverse=True):
            if b > fv and sr > 0:
                v = min(d.buy_orders[b], sr); orders.append(Order("TOMATOES",b,-v)); sr -= v
            else: break
        # Penny-jump clamped to FV
        bp, ap = min(bb+1, fvi-1), max(ba-1, fvi+1)
        if br > 0: orders.append(Order("TOMATOES",bp,br))
        if sr > 0: orders.append(Order("TOMATOES",ap,-sr))
        return orders
