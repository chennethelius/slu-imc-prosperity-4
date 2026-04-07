"""
Prosperity 4 — Robust TOMATO Market Maker + Stable EMERALD MM

TOMATOES: Spread-adaptive EMA + aggressive execution.
Tuned for realistic matching (worse mode, partial queue penetration).

Key parameters (optimized across all matching configs):
  - EMA alpha=0.027 (globally optimal)
  - Spread sensitivity=0.30 (faster FV tracking helps taking accuracy
    under realistic matching where passive fills are unreliable)
  - Flatten threshold=8 (earlier flattening reduces inventory risk
    when fills are scarce)
  - Backup spread=4 (wider backup avoids adverse selection on SUB window)

EMERALDS: fixed FV=10000, unchanged.
"""

from datamodel import Order, TradingState
import json


class Trader:
    LIMITS = {"EMERALDS": 80, "TOMATOES": 80}
    EMERALDS_FV = 10000

    EMA_BASE_ALPHA = 0.027
    SPREAD_REF = 14.0
    SPREAD_SENSITIVITY = 0.3
    FLATTEN_THRESH = 8
    BACKUP_SPREAD = 4

    def run(self, state: TradingState) -> tuple[dict[str, list[Order]], int, str]:
        try:
            data = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            data = {}

        result: dict[str, list[Order]] = {}
        for symbol, depth in state.order_depths.items():
            if symbol not in self.LIMITS:
                result[symbol] = []
                continue
            if not depth.buy_orders or not depth.sell_orders:
                result[symbol] = []
                continue

            pos = state.position.get(symbol, 0)
            limit = self.LIMITS[symbol]

            if symbol == "EMERALDS":
                result[symbol] = self._trade_emeralds(symbol, depth, pos, limit)
            elif symbol == "TOMATOES":
                result[symbol], data = self._trade_tomatoes(
                    symbol, depth, pos, limit, data
                )
            else:
                result[symbol] = []

        return result, 0, json.dumps(data)

    def _trade_emeralds(self, sym, depth, pos, limit):
        bb = max(depth.buy_orders)
        ba = min(depth.sell_orders)
        if bb >= ba:
            return []
        fv = self.EMERALDS_FV
        orders = []
        br, sr = limit - pos, limit + pos

        for ap in sorted(depth.sell_orders):
            if ap < fv and br > 0:
                v = min(-depth.sell_orders[ap], br)
                orders.append(Order(sym, ap, v)); br -= v
            else:
                break
        for bp in sorted(depth.buy_orders, reverse=True):
            if bp > fv and sr > 0:
                v = min(depth.buy_orders[bp], sr)
                orders.append(Order(sym, bp, -v)); sr -= v
            else:
                break

        if pos > 10 and fv in depth.buy_orders and sr > 0:
            v = min(depth.buy_orders[fv], sr)
            orders.append(Order(sym, fv, -v)); sr -= v
        elif pos < -10 and fv in depth.sell_orders and br > 0:
            v = min(-depth.sell_orders[fv], br)
            orders.append(Order(sym, fv, v)); br -= v

        b1 = min(bb + 1, fv - 1)
        a1 = max(ba - 1, fv + 1)
        sz = min(80, br)
        if sz > 0:
            orders.append(Order(sym, b1, sz)); br -= sz
        sz = min(80, sr)
        if sz > 0:
            orders.append(Order(sym, a1, -sz)); sr -= sz
        if br > 0:
            orders.append(Order(sym, fv - 3, br))
        if sr > 0:
            orders.append(Order(sym, fv + 3, -sr))
        return orders

    def _trade_tomatoes(self, sym, depth, pos, limit, data):
        bb = max(depth.buy_orders)
        ba = min(depth.sell_orders)
        if bb >= ba:
            return [], data

        mid = (bb + ba) / 2.0
        spread = ba - bb

        # ── 1. Spread-adaptive EMA fair value ──
        spread_ratio = spread / self.SPREAD_REF
        alpha = self.EMA_BASE_ALPHA * (1.0 + self.SPREAD_SENSITIVITY * (spread_ratio - 1.0))
        alpha = max(0.015, min(0.06, alpha))

        prev_ema = data.get("ema", mid)
        fv = alpha * mid + (1 - alpha) * prev_ema
        data["ema"] = fv

        fv_int = int(round(fv))
        orders = []
        br = limit - pos
        sr = limit + pos

        # ── 2. Aggressive taking ──
        for ap in sorted(depth.sell_orders):
            if ap < fv and br > 0:
                v = min(-depth.sell_orders[ap], br)
                orders.append(Order(sym, ap, v)); br -= v
            else:
                break
        for bp in sorted(depth.buy_orders, reverse=True):
            if bp > fv and sr > 0:
                v = min(depth.buy_orders[bp], sr)
                orders.append(Order(sym, bp, -v)); sr -= v
            else:
                break

        # ── 3. Zero-edge flatten ──
        if pos > self.FLATTEN_THRESH and fv_int in depth.buy_orders and sr > 0:
            v = min(depth.buy_orders[fv_int], sr)
            orders.append(Order(sym, fv_int, -v)); sr -= v
        elif pos < -self.FLATTEN_THRESH and fv_int in depth.sell_orders and br > 0:
            v = min(-depth.sell_orders[fv_int], br)
            orders.append(Order(sym, fv_int, v)); br -= v

        # ── 4. Penny-jump with edge guard ──
        b1 = bb + 1
        a1 = ba - 1
        if b1 >= fv_int:
            b1 = fv_int - 1
        if a1 <= fv_int:
            a1 = fv_int + 1

        sz = min(80, br)
        if sz > 0:
            orders.append(Order(sym, b1, sz)); br -= sz
        sz = min(80, sr)
        if sz > 0:
            orders.append(Order(sym, a1, -sz)); sr -= sz

        # ── 5. Backup layer ──
        if br > 0:
            orders.append(Order(sym, fv_int - self.BACKUP_SPREAD, br))
        if sr > 0:
            orders.append(Order(sym, fv_int + self.BACKUP_SPREAD, -sr))

        return orders, data
