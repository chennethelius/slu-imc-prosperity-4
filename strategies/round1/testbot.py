from datamodel import Order, TradingState

OSM = "ASH_COATED_OSMIUM"
PEP = "INTARIAN_PEPPER_ROOT"


class Trader:
    """
    Round 1 bot. Two products:
      OSM — inventory-skewed three-phase market-making at fair=10000.
      PEP — pure +80 buy-and-hold (exploits monotonic upward drift).
    """

    POSITION_LIMITS = {OSM: 80, PEP: 80}

    OSM_FAIR = 10000
    OSM_TAKE_WIDTH = 1
    OSM_CLEAR_WIDTH = 1
    OSM_MAKE_EDGE = 3
    OSM_MIN_EDGE = 1
    OSM_MIN_TW = 0
    OSM_SKEW_UNIT = 12
    OSM_VOLUME_LIMIT = 30

    PEP_TARGET = 80

    def bid(self):
        return 15

    def run(self, state: TradingState):
        orders: dict[str, list[Order]] = {}
        for symbol, depth in state.order_depths.items():
            if symbol not in self.POSITION_LIMITS:
                orders[symbol] = []
                continue
            pos = state.position.get(symbol, 0)
            if symbol == OSM:
                orders[symbol] = self._osmium(depth, pos)
            elif symbol == PEP:
                orders[symbol] = self._pepper(depth, pos)
            else:
                orders[symbol] = []
        return orders, 0, ""

    def _osmium(self, d, pos):
        if not d.buy_orders or not d.sell_orders:
            return []

        fair = self.OSM_FAIR
        lim = self.POSITION_LIMITS[OSM]
        cw = self.OSM_CLEAR_WIDTH
        vol_lim = self.OSM_VOLUME_LIMIT
        orders = []
        bought = sold = 0

        skew = round(pos / self.OSM_SKEW_UNIT)
        tw_ask = max(self.OSM_MIN_TW, self.OSM_TAKE_WIDTH + skew)
        tw_bid = max(self.OSM_MIN_TW, self.OSM_TAKE_WIDTH - skew)

        best_ask = min(d.sell_orders)
        if best_ask <= fair - tw_ask:
            q = min(-d.sell_orders[best_ask], lim - pos - bought)
            if q > 0:
                orders.append(Order(OSM, best_ask, q))
                bought += q

        best_bid = max(d.buy_orders)
        if best_bid >= fair + tw_bid:
            q = min(d.buy_orders[best_bid], lim + pos - sold)
            if q > 0:
                orders.append(Order(OSM, best_bid, -q))
                sold += q

        pos_after = pos + bought - sold
        clear_bid = fair - cw
        clear_ask = fair + cw
        if pos_after > 0:
            available = sum(v for p, v in d.buy_orders.items() if p >= clear_ask)
            q = min(lim + pos - sold, min(available, pos_after))
            if q > 0:
                orders.append(Order(OSM, clear_ask, -q))
                sold += q
        elif pos_after < 0:
            available = sum(-v for p, v in d.sell_orders.items() if p <= clear_bid)
            q = min(lim - pos - bought, min(available, -pos_after))
            if q > 0:
                orders.append(Order(OSM, clear_bid, q))
                bought += q

        bid_edge = max(self.OSM_MIN_EDGE, self.OSM_MAKE_EDGE + skew)
        ask_edge = max(self.OSM_MIN_EDGE, self.OSM_MAKE_EDGE - skew)
        outer_asks = [p for p in d.sell_orders if p > fair + ask_edge - 1]
        outer_bids = [p for p in d.buy_orders if p < fair - bid_edge + 1]
        if outer_asks and outer_bids:
            ask_anchor = min(outer_asks)
            bid_anchor = max(outer_bids)
            if ask_anchor <= fair + ask_edge and pos <= vol_lim:
                ask_anchor = fair + ask_edge + 1
            if bid_anchor >= fair - bid_edge and pos >= -vol_lim:
                bid_anchor = fair - bid_edge - 1
            quote_bid = bid_anchor + 1
            quote_ask = ask_anchor - 1
            buy_q = lim - pos - bought
            if buy_q > 0:
                orders.append(Order(OSM, quote_bid, buy_q))
            sell_q = lim + pos - sold
            if sell_q > 0:
                orders.append(Order(OSM, quote_ask, -sell_q))

        return orders

    def _pepper(self, d, pos):
        if not d.buy_orders or not d.sell_orders:
            return []
        need = self.PEP_TARGET - pos
        if need <= 0:
            return []
        lim = self.POSITION_LIMITS[PEP]
        to_buy = min(need, lim - pos)
        orders = []
        for price in sorted(d.sell_orders):
            if to_buy <= 0:
                break
            fill = min(-d.sell_orders[price], to_buy)
            if fill > 0:
                orders.append(Order(PEP, price, fill))
                to_buy -= fill
        if to_buy > 0:
            orders.append(Order(PEP, max(d.buy_orders) + 1, to_buy))
        return orders