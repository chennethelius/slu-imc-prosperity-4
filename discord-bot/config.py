"""Centralized configuration for the Discord intel bot."""

import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Discord Connection ---
TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
WATCH_CHANNELS = [
    int(c.strip())
    for c in os.getenv("WATCH_CHANNELS", "").split(",")
    if c.strip()
]

# Channel priority tiers (channel IDs)
HIGH_PRIORITY_CHANNELS = {
    int(c.strip())
    for c in os.getenv("HIGH_PRIORITY_CHANNELS", "").split(",")
    if c.strip()
}
LOW_PRIORITY_CHANNELS = {
    int(c.strip())
    for c in os.getenv("LOW_PRIORITY_CHANNELS", "").split(",")
    if c.strip()
}

# --- Competition State ---
CURRENT_ROUND = int(os.getenv("CURRENT_ROUND", "0"))  # 0 = tutorial/pre-round

# Known products — start with tutorial, extend as rounds drop
# Override via env: KNOWN_PRODUCTS=EMERALDS,TOMATOES,KELP,...
_default_products = "EMERALDS,TOMATOES,ASH_COATED_OSMIUM,INTARIAN_PEPPER_ROOT,OSMIUM,PEPPER_ROOT,PEPPER"
KNOWN_PRODUCTS = {
    p.strip()
    for p in os.getenv("KNOWN_PRODUCTS", _default_products).split(",")
    if p.strip()
}

# --- Scraping ---
RELEVANCE_THRESHOLD = int(os.getenv("RELEVANCE_THRESHOLD", "20"))
BACKFILL_LIMIT = int(os.getenv("BACKFILL_LIMIT", "2000"))
MIN_MESSAGE_LENGTH = 20

# --- Storage ---
BASE_DIR = Path(__file__).parent
STORAGE_DIR = BASE_DIR / "storage"
STORAGE_DIR.mkdir(exist_ok=True)
DB_PATH = Path(os.getenv("DB_PATH", str(STORAGE_DIR / "intel.db")))
CLAUDE_EXPORT_PATH = STORAGE_DIR / "claude_intel.json"

# --- Product Auto-Discovery ---
PRODUCT_PATTERN = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")
PRODUCT_BLACKLIST = {
    # Common acronyms that aren't products
    "EMA", "SMA", "VWAP", "RSI", "MACD", "OHLC", "OTC", "GTC", "IOC",
    "PNL", "P&L", "ROI", "NAV", "ETF", "BPS",
    "IMC", "API", "JSON", "CSV", "USD", "EUR", "GBP",
    "CPU", "GPU", "RAM", "SQL", "HTTP", "HTTPS", "URL",
    "TODO", "NOTE", "EDIT", "FIXED", "HINT", "TIP",
    "PYTHON", "NUMPY", "PANDAS", "SCIPY", "IMPORT",
    "TRUE", "FALSE", "NONE", "SELF", "CLASS", "DEF", "RETURN",
    "SUBMISSION", "SEASHELLS", "TRADER", "ORDER",
}

# --- Strategy Keywords ---
STRATEGY_KEYWORDS = {
    "market making", "market maker", "mm",
    "mean reversion", "mean-revert", "mean reverting",
    "ema", "sma", "vwap", "regression", "moving average",
    "arbitrage", "arb", "stat arb", "pairs trading", "pairs",
    "black-scholes", "black scholes", "greeks", "delta", "gamma", "vega", "theta",
    "fair value", "fair price", "mid price", "theoretical price",
    "spread", "bid-ask", "bid ask",
    "position limit", "position management",
    "pnl", "sharpe", "drawdown", "max drawdown",
    "backtesting", "backtest",
    "order book", "orderbook", "order depth",
    "momentum", "trend following",
    "basket", "etf", "nav", "conversion",
    "volatility", "vol surface", "implied vol",
    "inventory", "inventory risk",
}

# --- Code Block Detection ---
CODE_BLOCK_RE = re.compile(r"```(?:python|py)?\n(.*?)```", re.DOTALL)

# --- Scoring Weights ---
SCORE_CODE_BLOCK = 30
SCORE_PRODUCT_MENTION = 15  # per product, max 30
SCORE_PRODUCT_MAX = 30
SCORE_STRATEGY_KEYWORD = 10  # per keyword, max 20
SCORE_KEYWORD_MAX = 20
SCORE_NUMERIC_PARAM = 15
SCORE_LONG_MESSAGE = 5  # message > 200 chars
SCORE_REPLY_HIGH = 10  # reply to score >= 60
SCORE_ATTACHMENT = 5

CHANNEL_MULTIPLIER_HIGH = 1.2
CHANNEL_MULTIPLIER_DEFAULT = 1.0
CHANNEL_MULTIPLIER_LOW = 0.6
