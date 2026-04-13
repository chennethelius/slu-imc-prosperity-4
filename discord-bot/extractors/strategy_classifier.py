"""Classify strategy type from message content."""

from config import STRATEGY_KEYWORDS

# Strategy type patterns — order matters (first match wins for classification)
STRATEGY_TYPES = [
    ("market_making", {
        "market making", "market maker", "mm",
        "spread", "bid-ask", "bid ask",
        "inventory", "inventory risk",
    }),
    ("mean_reversion", {
        "mean reversion", "mean-revert", "mean reverting",
        "ema", "sma", "vwap", "moving average", "regression",
    }),
    ("arbitrage", {
        "arbitrage", "arb", "stat arb", "pairs trading", "pairs",
        "basket", "etf", "nav", "conversion",
    }),
    ("options", {
        "black-scholes", "black scholes", "greeks",
        "delta", "gamma", "vega", "theta",
        "volatility", "vol surface", "implied vol",
        "strike",
    }),
    ("momentum", {
        "momentum", "trend following",
    }),
]


def classify_strategy(content: str) -> str | None:
    """Classify the dominant strategy type mentioned in a message.

    Returns the strategy type with the most keyword matches, or None.
    """
    lower = content.lower()
    best_type = None
    best_count = 0

    for strategy_type, keywords in STRATEGY_TYPES:
        count = sum(1 for kw in keywords if kw in lower)
        if count > best_count:
            best_count = count
            best_type = strategy_type

    return best_type if best_count > 0 else None


def detect_strategy_keywords(content: str) -> list[str]:
    """Detect all strategy-related keywords in content."""
    lower = content.lower()
    return [kw for kw in STRATEGY_KEYWORDS if kw in lower]
