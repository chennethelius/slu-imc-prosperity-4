import json

from datamodel import Order, TradingState

HYD = "HYDROGEL_PACK"


class Logger:
    def __init__(self) -> None:
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects, sep=" ", end="\n") -> None:
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state, orders, conversions, trader_data) -> None:
        base_length = len(
            self.to_json(
                [
                    self.compress_state(state, ""),
                    self.compress_orders(orders),
                    conversions,
                    "",
                    "",
                ]
            )
        )
        max_item_length = (self.max_log_length - base_length) // 2
        print(
            self.to_json(
                [
                    self.compress_state(state, self.truncate(state.traderData, max_item_length)),
                    self.compress_orders(orders),
                    conversions,
                    self.truncate(trader_data, max_item_length),
                    self.truncate(self.logs, max_item_length),
                ]
            )
        )
        self.logs = ""

    def compress_state(self, state, trader_data):
        return [
            state.timestamp,
            trader_data,
            self.compress_listings(state.listings),
            self.compress_order_depths(state.order_depths),
            self.compress_trades(state.own_trades),
            self.compress_trades(state.market_trades),
            state.position,
            self.compress_observations(state.observations),
        ]

    def compress_listings(self, listings):
        return [[l.symbol, l.product, l.denomination] for l in listings.values()]

    def compress_order_depths(self, order_depths):
        return {s: [od.buy_orders, od.sell_orders] for s, od in order_depths.items()}

    def compress_trades(self, trades):
        return [
            [t.symbol, t.price, t.quantity, t.buyer, t.seller, t.timestamp]
            for arr in trades.values()
            for t in arr
        ]

    def compress_observations(self, obs):
        co = {}
        for product, observation in obs.conversionObservations.items():
            co[product] = [
                observation.bidPrice,
                observation.askPrice,
                observation.transportFees,
                observation.exportTariff,
                observation.importTariff,
                observation.sugarPrice,
                observation.sunlightIndex,
            ]
        return [obs.plainValueObservations, co]

    def compress_orders(self, orders):
        return {s: [[o.symbol, o.price, o.quantity] for o in ol] for s, ol in orders.items()}

    def to_json(self, value) -> str:
        return json.dumps(value, cls=ProsperityEncoder, separators=(",", ":"))

    def truncate(self, value: str, max_length: int) -> str:
        if len(value) <= max_length:
            return value
        return value[: max_length - 3] + "..."


class ProsperityEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, int) and abs(o) >= 2**53:
            return str(o)
        return super().default(o)


logger = Logger()


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
    MAKE_EDGE = 4
    SKEW_UNIT = 40
    QUOTE_SIZE = 20
    MOMENTUM_THRESHOLD = 0.8

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
            momentum = 0.0 if prev is None else ema - prev
            td["ema"] = ema
            pos = state.position.get(HYD, 0)
            orders[HYD] = self._mm(depth, pos, ema, momentum, bb, ba)
            logger.print(
                f"pos={pos} ema={ema:.2f} mom={momentum:+.3f} bb={bb} ba={ba} orders={len(orders[HYD])}"
            )

        trader_data = json.dumps(td)
        logger.flush(state, orders, 0, trader_data)
        return orders, 0, trader_data

    def _mm(self, d, pos, fair, mom, bb, ba):
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
        bid_px = round(fair) - bid_edge
        ask_px = round(fair) + ask_edge
        if bid_px >= ba:
            bid_px = ba - 1
        if ask_px <= bb:
            ask_px = bb + 1
        post_bid = mom > -self.MOMENTUM_THRESHOLD
        post_ask = mom < self.MOMENTUM_THRESHOLD
        if bid_px < ask_px:
            br = self.LIMIT - pos - bought
            sr = self.LIMIT + pos - sold
            if post_bid and br > 0:
                out.append(Order(HYD, bid_px, min(br, self.QUOTE_SIZE)))
            if post_ask and sr > 0:
                out.append(Order(HYD, ask_px, -min(sr, self.QUOTE_SIZE)))
        return out
