# Prosperity Workspace — Claude Context

## What This Is

Automated development workflow for the IMC Prosperity 4 algorithmic trading challenge.
Strategies go in `strategies/`, get backtested via the Rust backtester, and results are
visualized locally or read directly by Claude for analysis.

## Project Layout

```
strategies/         Python Trader class files (one per strategy)
  round1-5/         Organized by round
  lib/              Shared utility code (copied into strategy at submission)
backtester/         Git submodule: GeyzsoN/prosperity_rust_backtester
  datasets/         Round data goes here (prices/trades CSVs, submission logs)
  runs/             Backtester output (metrics, logs, artifacts)
visualizer/         Git submodule: kevin-fu1/imc-prosperity-4-visualizer
runs/               Symlinked/copied backtest outputs for easy access
scripts/            Pipeline tooling (backtest, analyze, compare, visualize)
discord-bot/        Discord scraper for community intel
notebooks/          Jupyter notebooks for data exploration
```

## Quick Commands

```bash
# Run a backtest (wraps the Rust backtester CLI)
./scripts/run_backtest.sh strategies/round1/my_strategy.py tutorial
./scripts/run_backtest.sh strategies/round1/my_strategy.py round1
./scripts/run_backtest.sh strategies/round1/my_strategy.py round1 --day=-1

# Or use the backtester directly (auto-picks trader + dataset)
cd backtester && make backtest
cd backtester && make tutorial
cd backtester && make round1 TRADER=../strategies/round1/my_strategy.py

# Compare two runs
python scripts/compare.py runs/<run_a> runs/<run_b>

# Start visualizer (keep running in background)
cd visualizer && pnpm dev

# Analyze a run (prints summary Claude can read)
python scripts/analyze.py runs/<run_id>
```

### Dataset Setup

Round data goes in `backtester/datasets/roundN/` as CSV pairs:
- `prices_round_N_day_D.csv` — order book snapshots
- `trades_round_N_day_D.csv` — executed trades

Download these from the IMC Prosperity portal after each round opens.
The backtester also accepts `submission.log` files from the portal.
Tutorial data (EMERALDS + TOMATOES) is bundled in `backtester/datasets/tutorial/`.

---

## IMC Prosperity 4 — Complete Technical Reference

### Competition Structure

- Multi-round algorithmic trading competition by IMC Trading
- Each round introduces new tradeable products on a simulated exchange
- You submit a single Python file containing a `Trader` class
- Your algorithm runs against historical/simulated market data
- Ranked on PnL in "seashells" (in-game currency)
- Each round lasts ~1 week; cumulative scoring across all rounds

### Submission Constraints (CRITICAL)

1. **Single file**: Your `Trader` class + all helpers must be in ONE Python file
2. **Standard library only**: No numpy, pandas, scipy, or any third-party imports
3. **No network access**: Pure computation only
4. **Time limit**: ~100ms per tick (soft limit, varies)
5. **No persistent state** except via `trader_data` string (serialized between ticks)
6. **Import whitelist**: `json`, `math`, `statistics`, `collections`, `typing`, `copy`, `itertools`, `functools`, `string`, `re`, `abc`, `enum`, `dataclasses`, `operator`

### The Trader Class Interface

```python
from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List
import json

class Trader:
    def run(self, state: TradingState) -> tuple[dict[str, List[Order]], int, str]:
        """
        Called once per timestamp (tick).

        Args:
            state: Current market state (order books, positions, trades, observations)

        Returns:
            result: dict mapping symbol -> list of Order objects (your orders)
            conversions: int (number of conversions to execute, for arb products)
            trader_data: str (JSON string persisted to next tick via state.traderData)
        """
        result = {}
        conversions = 0
        trader_data = ""
        return result, conversions, trader_data
```

### Complete Data Model

```python
class TradingState:
    traderData: str                                    # Your persisted state from previous tick (JSON string)
    timestamp: int                                     # Current tick (day * 1_000_000 + within_day_tick)
    listings: dict[str, Listing]                       # All tradeable products
    order_depths: dict[str, OrderDepth]                # Order books per symbol
    own_trades: dict[str, list[Trade]]                 # Your fills since last tick
    market_trades: dict[str, list[Trade]]              # All market trades since last tick
    position: dict[str, int]                           # Your current positions per symbol
    observations: Observation                          # External signals

class Listing:
    symbol: str                                        # e.g. "EMERALDS"
    product: str                                       # Usually same as symbol
    denomination: str                                  # Usually "SEASHELLS"

class OrderDepth:
    buy_orders: dict[int, int]                         # price -> volume (POSITIVE)
    sell_orders: dict[int, int]                        # price -> volume (NEGATIVE)
    # Prices are ints. Best bid = max(buy_orders.keys()), best ask = min(sell_orders.keys())

class Order:
    symbol: str
    price: int
    quantity: int                                      # POSITIVE = buy, NEGATIVE = sell

class Trade:
    symbol: str
    price: int
    quantity: int                                      # Always positive
    buyer: str                                         # UserId or "SUBMISSION" (you)
    seller: str                                        # UserId or "SUBMISSION" (you)
    timestamp: int

class Observation:
    plainValueObservations: dict[str, float]           # e.g. {"SUNLIGHT": 2500.0}
    conversionObservations: dict[str, ConversionObservation]

class ConversionObservation:
    bidPrice: float                                    # Foreign exchange bid
    askPrice: float                                    # Foreign exchange ask
    transportFees: float
    exportTariff: float
    importTariff: float
    sugarPrice: float
    sunlightIndex: float
```

### Position Limits (Prosperity 4)

These are HARD LIMITS. Orders that would exceed them are rejected silently.

| Product | Position Limit |
|---------|---------------|
| EMERALDS | 80 |
| TOMATOES | 80 |
| RAINFOREST_RESIN | 50 |
| KELP | 50 |
| SQUID_INK | 50 |
| CROISSANTS | 250 |
| JAMS | 350 |
| DJEMBES | 60 |
| PICNIC_BASKET1 | 60 |
| PICNIC_BASKET2 | 100 |
| VOLCANIC_ROCK | 400 |
| VOLCANIC_ROCK_VOUCHER_9500 | 200 |
| VOLCANIC_ROCK_VOUCHER_9750 | 200 |
| VOLCANIC_ROCK_VOUCHER_10000 | 200 |
| VOLCANIC_ROCK_VOUCHER_10250 | 200 |
| VOLCANIC_ROCK_VOUCHER_10500 | 200 |
| MAGNIFICENT_MACARONS | 75 |

> **Always check**: `state.position.get(symbol, 0)` before placing orders.
> Max buy qty = limit - current_position. Max sell qty = limit + current_position.

### Order Execution Rules

1. Orders are **limit orders only** — they fill against the simulated book
2. Your buy orders fill against sell_orders (asks); your sell orders fill against buy_orders (bids)
3. Orders that cross the book fill immediately at the resting price (price improvement)
4. Unfilled orders do NOT persist — they are cancelled at end of tick
5. You can place multiple orders per symbol per tick
6. Self-trading is possible and should be avoided

### Timestamp Structure

- `timestamp = day * 1_000_000 + within_day_tick`
- Within-day ticks: 0, 100, 200, ..., 999900 (10,000 ticks per day)
- Day 0 starts at timestamp 0, Day 1 at 1000000, etc.
- Most rounds run 3-5 days

### The Logger Class (Required for Visualizer Compatibility)

Your strategy MUST include this Logger class to produce output compatible with the visualizer:

```python
import json
from datamodel import Order, TradingState

class Logger:
    def __init__(self) -> None:
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects, sep=" ", end="\n") -> None:
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state: TradingState, orders: dict[str, list[Order]], conversions: int, trader_data: str) -> None:
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

    def compress_state(self, state: TradingState, trader_data: str) -> list:
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

    def compress_listings(self, listings: dict) -> list:
        return [[l.symbol, l.product, l.denomination] for l in listings.values()]

    def compress_order_depths(self, order_depths: dict) -> dict:
        return {s: [od.buy_orders, od.sell_orders] for s, od in order_depths.items()}

    def compress_trades(self, trades: dict) -> list:
        return [
            [t.symbol, t.price, t.quantity, t.buyer, t.seller, t.timestamp]
            for arr in trades.values()
            for t in arr
        ]

    def compress_observations(self, obs) -> list:
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

    def compress_orders(self, orders: dict) -> dict:
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
```

Usage in your Trader class:
```python
logger = Logger()

class Trader:
    def run(self, state: TradingState) -> tuple[dict, int, str]:
        result = {}
        conversions = 0
        trader_data = ""

        # Use logger.print() instead of print() for debug output
        logger.print(f"Position: {state.position}")

        # ... your strategy logic ...

        # MUST call flush at the end of run()
        logger.flush(state, result, conversions, trader_data)
        return result, conversions, trader_data
```

### Typical Product Patterns (from past rounds)

| Pattern | Products | Strategy Approach |
|---------|----------|-------------------|
| Tutorial | EMERALDS (~10000), TOMATOES (~5000) | Market making basics |
| Stable fair value | RAINFOREST_RESIN (~10000) | Market make around known fair value |
| Mean-reverting | KELP, SQUID_INK | EMA/regression, fade deviations |
| Basket/ETF arb | PICNIC_BASKET = weighted sum of components | Stat arb on spread vs NAV |
| Cross-exchange arb | Products with conversionObservations | Factor in transport + tariffs |
| Options | VOLCANIC_ROCK_VOUCHER_* | Black-Scholes (implement from scratch, no scipy) |
| External signals | Products with plainValueObservations | Correlate signals to price movement |

### Common Pitfalls

1. **Exceeding position limits silently** — orders are just dropped, no error
2. **sell_orders volumes are NEGATIVE** — always use `abs()` when computing quantities
3. **trader_data must be a string** — use `json.dumps()`/`json.loads()` for structured state
4. **No order persistence** — every tick starts fresh, no GTC orders
5. **Integer prices** — the exchange uses int prices, don't submit floats
6. **Self-trade** — if you place a buy at 100 and sell at 100, they can match

---

## Backtest Output Format

After running `./scripts/run_backtest.sh`, outputs land in `runs/<run_id>/`:

| File | What Claude Should Read It For |
|------|-------------------------------|
| `submission.log` | Raw log (feeds visualizer). Large — read only if needed for specific tick analysis |
| `summary.txt` | Human-readable summary (start here) |
| `metrics.json` | Structured: total PnL, per-product PnL, Sharpe, max drawdown, trade count |
| `pnl_by_product.csv` | Time series: timestamp, product, cumulative PnL |
| `trades.csv` | Every fill: timestamp, symbol, price, qty, side, counterparty |
| `activity.csv` | Order book snapshots: timestamp, product, bid/ask levels, mid, PnL |

### Reading Results — Workflow for Claude

1. Start with `summary.txt` — quick overview
2. For deeper analysis, read `metrics.json` — structured numbers
3. For specific product issues, read `pnl_by_product.csv` filtered to that product
4. For trade-level debugging, read `trades.csv` around the problematic timestamps
5. Suggest the user open the visualizer for order book dynamics

---

## Visualizer Integration

The visualizer runs locally at `http://localhost:5173`.
Auto-load a backtest result: `http://localhost:5173/?open=http://localhost:8080/<run_id>/submission.log`

The visualizer renders: PnL curves, position charts, candlesticks (OHLC), order overlays,
conversion prices, transport fees, environment signals, and per-tick drill-down.

Charts can be exported as PNG via Highcharts export menu for Claude to read as images.

---

## Discord Bot

The Discord bot scrapes strategy discussions and stores them in `discord-bot/storage/`.
Claude can read `discord-bot/storage/scraped_strategies.json` for community intel.
