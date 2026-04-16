import json

from datamodel import Order, TradingState


class Trader:
    """
    pepper_long: always target +80 pepper, load from tick 0. No signal,
    no flip. Pure directional bet that the submission day drifts up.
    OSM uses slow EMA fair value seeded at 10000 (3-day data shows ~+3 drift).
    """

    POSITION_LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}

    OSMIUM_FAIR_SEED = 10000.0
    OSMIUM_FAIR_ALPHA = 0.001
    OSMIUM_TAKE_WIDTH = 1
    OSMIUM_CLEAR_WIDTH = 0
    OSMIUM_INVENTORY_SKEW_THRESHOLD = 20

    PEPPER_TARGET_POSITION = 80

    def bid(self):
        return 15

    def run(self, state: TradingState):
        td = json.loads(state.traderData) if state.traderData else {}
        osm_fair = td.get("osm_fair", self.OSMIUM_FAIR_SEED)

        orders_by_symbol: dict[str, list[Order]] = {}
        for symbol, order_depth in state.order_depths.items():
            if symbol not in self.POSITION_LIMITS:
                orders_by_symbol[symbol] = []
                continue
            current_position = state.position.get(symbol, 0)
            if symbol == "ASH_COATED_OSMIUM":
                if order_depth.buy_orders and order_depth.sell_orders:
                    mid = (max(order_depth.buy_orders) + min(order_depth.sell_orders)) / 2
                    osm_fair = self.OSMIUM_FAIR_ALPHA * mid + (1 - self.OSMIUM_FAIR_ALPHA) * osm_fair
                orders_by_symbol[symbol] = self._osmium(order_depth, current_position, int(round(osm_fair)))
            elif symbol == "INTARIAN_PEPPER_ROOT":
                orders_by_symbol[symbol] = self._pepper(symbol, order_depth, current_position)
            else:
                orders_by_symbol[symbol] = []

        trader_data = json.dumps({"osm_fair": osm_fair})
        return orders_by_symbol, 0, trader_data

    def _osmium(self, order_depth, current_position, fair_value):
        if not order_depth.buy_orders or not order_depth.sell_orders:
            return []
        position_limit = self.POSITION_LIMITS["ASH_COATED_OSMIUM"]
        take_width = self.OSMIUM_TAKE_WIDTH
        clear_width = self.OSMIUM_CLEAR_WIDTH
        inventory_skew_threshold = self.OSMIUM_INVENTORY_SKEW_THRESHOLD
        orders = []
        buy_volume_used = 0
        sell_volume_used = 0

        best_ask = min(order_depth.sell_orders)
        best_ask_volume = -order_depth.sell_orders[best_ask]
        if best_ask <= fair_value - take_width:
            buy_quantity = min(best_ask_volume, position_limit - current_position - buy_volume_used)
            if buy_quantity > 0:
                orders.append(Order("ASH_COATED_OSMIUM", best_ask, buy_quantity))
                buy_volume_used += buy_quantity
        best_bid = max(order_depth.buy_orders)
        best_bid_volume = order_depth.buy_orders[best_bid]
        if best_bid >= fair_value + take_width:
            sell_quantity = min(best_bid_volume, position_limit + current_position - sell_volume_used)
            if sell_quantity > 0:
                orders.append(Order("ASH_COATED_OSMIUM", best_bid, -sell_quantity))
                sell_volume_used += sell_quantity

        position_after_take = current_position + buy_volume_used - sell_volume_used
        fair_bid_price = fair_value - clear_width
        fair_ask_price = fair_value + clear_width
        if position_after_take > 0:
            clearable_quantity = sum(v for p, v in order_depth.buy_orders.items() if p >= fair_ask_price)
            clearable_quantity = min(clearable_quantity, position_after_take)
            clear_sell_quantity = min(position_limit + current_position - sell_volume_used, clearable_quantity)
            if clear_sell_quantity > 0:
                orders.append(Order("ASH_COATED_OSMIUM", fair_ask_price, -clear_sell_quantity))
                sell_volume_used += clear_sell_quantity
        if position_after_take < 0:
            clearable_quantity = sum(-v for p, v in order_depth.sell_orders.items() if p <= fair_bid_price)
            clearable_quantity = min(clearable_quantity, -position_after_take)
            clear_buy_quantity = min(position_limit - current_position - buy_volume_used, clearable_quantity)
            if clear_buy_quantity > 0:
                orders.append(Order("ASH_COATED_OSMIUM", fair_bid_price, clear_buy_quantity))
                buy_volume_used += clear_buy_quantity

        asks_above_fair = [p for p in order_depth.sell_orders if p > fair_value + 1]
        bids_below_fair = [p for p in order_depth.buy_orders if p < fair_value - 1]
        if asks_above_fair and bids_below_fair:
            lowest_ask_above_fair = min(asks_above_fair)
            highest_bid_below_fair = max(bids_below_fair)
            if lowest_ask_above_fair <= fair_value + 2 and current_position <= inventory_skew_threshold:
                lowest_ask_above_fair = fair_value + 3
            if highest_bid_below_fair >= fair_value - 2 and current_position >= -inventory_skew_threshold:
                highest_bid_below_fair = fair_value - 3
            quote_bid_price = highest_bid_below_fair + 1
            quote_ask_price = lowest_ask_above_fair - 1
            make_buy_quantity = position_limit - current_position - buy_volume_used
            if make_buy_quantity > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(quote_bid_price), make_buy_quantity))
            make_sell_quantity = position_limit + current_position - sell_volume_used
            if make_sell_quantity > 0:
                orders.append(Order("ASH_COATED_OSMIUM", int(quote_ask_price), -make_sell_quantity))
        return orders

    def _pepper(self, symbol, order_depth, current_position):
        if not order_depth.buy_orders or not order_depth.sell_orders:
            return []
        asks_ascending = sorted(order_depth.sell_orders.keys())
        bids_descending = sorted(order_depth.buy_orders.keys(), reverse=True)
        position_limit = self.POSITION_LIMITS["INTARIAN_PEPPER_ROOT"]
        quantity_needed = self.PEPPER_TARGET_POSITION - current_position
        orders = []
        if quantity_needed > 0:
            remaining_to_buy = min(quantity_needed, position_limit - current_position)
            for ask_price in asks_ascending:
                if remaining_to_buy <= 0:
                    break
                fill_volume = min(-order_depth.sell_orders[ask_price], remaining_to_buy)
                if fill_volume > 0:
                    orders.append(Order(symbol, ask_price, fill_volume))
                    remaining_to_buy -= fill_volume
            if remaining_to_buy > 0:
                passive_bid_price = bids_descending[0] + 1
                orders.append(Order(symbol, passive_bid_price, remaining_to_buy))
        return orders
