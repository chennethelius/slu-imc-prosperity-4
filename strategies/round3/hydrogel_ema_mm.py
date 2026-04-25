import json

from datamodel import Order, TradingState

HYD = "HYDROGEL_PACK"


class Trader:
    """
    EMA mean-reversion market maker on HYDROGEL_PACK.

    Tracks an EMA of the mid as fair value. Takes the book when it crosses
    fair by TAKE_WIDTH, clears excess inventory at fair +/- CLEAR_WIDTH, and
    posts inventory-skewed make quotes at fair +/- MAKE_EDGE (penny inside
    the prevailing top-of-book where possible).
    """

    LIMIT = 200
    EMA_ALPHA = 0.10
    TAKE_WIDTH = 2
    CLEAR_WIDTH = 1
    MAKE_EDGE = 2
    SKEW_UNIT = 40
    QUOTE_SIZE = 20

    def bid(self):
        return 15

    def run(self, state: TradingState):
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}

        orders: dict[str, list[Order]] = {}
        depth = state.order_depths.get(HYD)
        if depth and depth.buy_orders and depth.sell_orders:
            bb = max(depth.buy_orders)
            ba = min(depth.sell_orders)
            mid = (bb + ba) / 2.0
            prev = td.get("ema")
            ema = mid if prev is None else self.EMA_ALPHA * mid + (1 - self.EMA_ALPHA) * prev
            td["ema"] = ema
            orders[HYD] = self._mm(depth, state.position.get(HYD, 0), ema, bb, ba)

        return orders, 0, json.dumps(td)

    def _mm(self, d, pos, fair, bb, ba):
        out = []
        bought = sold = 0
        skew = round(pos / self.SKEW_UNIT)
        tw_ask = max(0, self.TAKE_WIDTH + skew)
        tw_bid = max(0, self.TAKE_WIDTH - skew)

        for price in sorted(d.sell_orders):
            if price > fair - tw_ask:
                break
            cap = self.LIMIT - pos - bought
            if cap <= 0:
                break
            q = min(-d.sell_orders[price], cap)
            out.append(Order(HYD, price, q))
            bought += q

        for price in sorted(d.buy_orders, reverse=True):
            if price < fair + tw_bid:
                break
            cap = self.LIMIT + pos - sold
            if cap <= 0:
                break
            q = min(d.buy_orders[price], cap)
            out.append(Order(HYD, price, -q))
            sold += q

        pos_after = pos + bought - sold
        if pos_after > 0:
            cp = round(fair) + self.CLEAR_WIDTH
            avail = sum(v for p, v in d.buy_orders.items() if p >= cp)
            q = min(self.LIMIT + pos - sold, avail, pos_after)
            if q > 0:
                out.append(Order(HYD, cp, -q))
                sold += q
        elif pos_after < 0:
            cp = round(fair) - self.CLEAR_WIDTH
            avail = sum(-v for p, v in d.sell_orders.items() if p <= cp)
            q = min(self.LIMIT - pos - bought, avail, -pos_after)
            if q > 0:
                out.append(Order(HYD, cp, q))
                bought += q

        bid_edge = max(1, self.MAKE_EDGE + skew)
        ask_edge = max(1, self.MAKE_EDGE - skew)
        bid_px = min(bb + 1, round(fair) - bid_edge)
        ask_px = max(ba - 1, round(fair) + ask_edge)
        if bid_px < ask_px:
            br = self.LIMIT - pos - bought
            sr = self.LIMIT + pos - sold
            if br > 0:
                out.append(Order(HYD, bid_px, min(br, self.QUOTE_SIZE)))
            if sr > 0:
                out.append(Order(HYD, ask_px, -min(sr, self.QUOTE_SIZE)))
        return out
