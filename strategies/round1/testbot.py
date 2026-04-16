import json

from datamodel import Order, TradingState

OSM = "ASH_COATED_OSMIUM"
PEP = "INTARIAN_PEPPER_ROOT"


class Trader:
    """
    Round 1 bot.
      OSM — inventory-skewed three-phase market-making at fair=10000.
      PEP — drift-calibrated +7 entry filter for 200 ticks, then pure take to +80.
    """

    LIMIT = 80
    OSM_FAIR = 10000
    OSM_TAKE_WIDTH = 2
    OSM_CLEAR_WIDTH = 2
    OSM_MAKE_EDGE = 3
    OSM_SKEW_UNIT = 18
    OSM_VOLUME_LIMIT = 30

    PEP_DRIFT = 0.100188
    PEP_CALIB_TICKS = 10
    PEP_ENTRY_TAKE = 7
    PEP_ENTRY_TIMEOUT = 200

    def bid(self):
        return 15

    def run(self, state: TradingState):
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}
        orders: dict[str, list[Order]] = {}
        for symbol, depth in state.order_depths.items():
            pos = state.position.get(symbol, 0)
            if symbol == OSM:
                orders[symbol] = self._osmium(depth, pos)
            elif symbol == PEP:
                orders[symbol] = self._pepper(depth, pos, state.timestamp, td)
        return orders, 0, json.dumps(td)

    def _osmium(self, d, pos):
        if not d.buy_orders or not d.sell_orders:
            return []
        fair = self.OSM_FAIR
        lim = self.LIMIT
        orders = []
        bought = sold = 0

        skew = round(pos / self.OSM_SKEW_UNIT)
        tw_ask = max(0, self.OSM_TAKE_WIDTH + skew)
        tw_bid = max(0, self.OSM_TAKE_WIDTH - skew)

        best_ask = min(d.sell_orders)
        if best_ask <= fair - tw_ask:
            q = min(-d.sell_orders[best_ask], lim - pos)
            if q > 0:
                orders.append(Order(OSM, best_ask, q))
                bought = q

        best_bid = max(d.buy_orders)
        if best_bid >= fair + tw_bid:
            q = min(d.buy_orders[best_bid], lim + pos)
            if q > 0:
                orders.append(Order(OSM, best_bid, -q))
                sold = q

        pos_after = pos + bought - sold
        if pos_after > 0:
            clear_ask = fair + self.OSM_CLEAR_WIDTH
            avail = sum(v for p, v in d.buy_orders.items() if p >= clear_ask)
            q = min(lim + pos - sold, avail, pos_after)
            if q > 0:
                orders.append(Order(OSM, clear_ask, -q))
                sold += q
        elif pos_after < 0:
            clear_bid = fair - self.OSM_CLEAR_WIDTH
            avail = sum(-v for p, v in d.sell_orders.items() if p <= clear_bid)
            q = min(lim - pos - bought, avail, -pos_after)
            if q > 0:
                orders.append(Order(OSM, clear_bid, q))
                bought += q

        bid_edge = max(1, self.OSM_MAKE_EDGE + skew)
        ask_edge = max(1, self.OSM_MAKE_EDGE - skew)
        outer_asks = [p for p in d.sell_orders if p > fair + ask_edge - 1]
        outer_bids = [p for p in d.buy_orders if p < fair - bid_edge + 1]
        if outer_asks and outer_bids:
            ask_anchor = min(outer_asks)
            bid_anchor = max(outer_bids)
            if ask_anchor <= fair + ask_edge and pos <= self.OSM_VOLUME_LIMIT:
                ask_anchor = fair + ask_edge + 1
            if bid_anchor >= fair - bid_edge and pos >= -self.OSM_VOLUME_LIMIT:
                bid_anchor = fair - bid_edge - 1
            buy_q = lim - pos - bought
            if buy_q > 0:
                orders.append(Order(OSM, bid_anchor + 1, buy_q))
            sell_q = lim + pos - sold
            if sell_q > 0:
                orders.append(Order(OSM, ask_anchor - 1, -sell_q))

        return orders

    def _pepper(self, d, pos, timestamp, td):
        if not d.buy_orders or not d.sell_orders:
            return []
        remaining = self.LIMIT - pos
        if remaining <= 0:
            return []
        tick = timestamp // 100

        samples = td.get("_pep_samples", [])
        if tick < self.PEP_CALIB_TICKS:
            mid = (max(d.buy_orders) + min(d.sell_orders)) / 2.0
            samples.append(mid - self.PEP_DRIFT * tick)
            td["_pep_samples"] = samples
        intercept = sum(samples) / len(samples) if samples else 0
        fair = intercept + self.PEP_DRIFT * tick

        cap = fair + self.PEP_ENTRY_TAKE if tick < self.PEP_ENTRY_TIMEOUT else float("inf")
        orders = []
        for price in sorted(d.sell_orders):
            if remaining <= 0 or price > cap:
                break
            fill = min(-d.sell_orders[price], remaining)
            if fill > 0:
                orders.append(Order(PEP, price, fill))
                remaining -= fill
        return orders
