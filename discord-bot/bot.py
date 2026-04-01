#!/usr/bin/env python3
"""
Discord bot for scraping IMC Prosperity strategy discussions.

Monitors configured channels, extracts code blocks, strategy mentions,
and product signals. Stores everything in a structured JSON file that
Claude can read for community intel.

Setup:
    1. Copy .env.example to .env and fill in your bot token + channel IDs
    2. pip install -r requirements.txt
    3. python bot.py

Bot permissions needed: Read Messages, Read Message History, Message Content Intent
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import discord
    from dotenv import load_dotenv
except ImportError:
    print("ERROR: Install dependencies first: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "")
WATCH_CHANNELS = [int(c.strip()) for c in os.getenv("WATCH_CHANNELS", "").split(",") if c.strip()]
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

STORAGE_DIR = Path(__file__).parent / "storage"
STORAGE_DIR.mkdir(exist_ok=True)
STRATEGIES_FILE = STORAGE_DIR / "scraped_strategies.json"
SIGNALS_FILE = STORAGE_DIR / "scraped_signals.json"

# Known Prosperity 4 products for signal detection
PRODUCTS = {
    "RAINFOREST_RESIN", "KELP", "SQUID_INK",
    "CROISSANTS", "JAMS", "DJEMBES",
    "PICNIC_BASKET1", "PICNIC_BASKET2",
    "VOLCANIC_ROCK", "VOLCANIC_ROCK_VOUCHER",
    "MAGNIFICENT_MACARONS",
}

# Strategy-related keywords
STRATEGY_KEYWORDS = {
    "market making", "market maker", "mm",
    "mean reversion", "mean-revert", "ema", "sma", "regression",
    "arbitrage", "arb", "stat arb", "pairs",
    "black-scholes", "black scholes", "greeks", "delta", "gamma", "vega",
    "fair value", "fair price", "mid price",
    "spread", "bid-ask", "position limit",
    "pnl", "sharpe", "drawdown",
    "backtesting", "backtest",
}

CODE_BLOCK_RE = re.compile(r"```(?:python|py)?\n(.*?)```", re.DOTALL)


def load_json(path: Path) -> list:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


def save_json(path: Path, data: list):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def extract_code_blocks(content: str) -> list[str]:
    return CODE_BLOCK_RE.findall(content)


def detect_products(content: str) -> list[str]:
    upper = content.upper()
    return [p for p in PRODUCTS if p in upper]


def detect_strategy_keywords(content: str) -> list[str]:
    lower = content.lower()
    return [kw for kw in STRATEGY_KEYWORDS if kw in lower]


def is_relevant(content: str) -> bool:
    """Check if a message is worth scraping."""
    if len(content) < 20:
        return False
    has_code = bool(CODE_BLOCK_RE.search(content))
    has_products = bool(detect_products(content))
    has_keywords = bool(detect_strategy_keywords(content))
    return has_code or has_products or has_keywords


class ProsperityBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        super().__init__(intents=intents)

    async def on_ready(self):
        print(f"[bot] Logged in as {self.user}")
        print(f"[bot] Watching {len(WATCH_CHANNELS)} channel(s)")
        print(f"[bot] Storage: {STORAGE_DIR}")

    async def on_message(self, message: discord.Message):
        # Skip bot messages
        if message.author.bot:
            return

        # Skip if not in a watched channel (if channels are configured)
        if WATCH_CHANNELS and message.channel.id not in WATCH_CHANNELS:
            return

        content = message.content
        if not is_relevant(content):
            return

        timestamp = message.created_at.isoformat()
        author = str(message.author)
        channel = str(message.channel)
        code_blocks = extract_code_blocks(content)
        products = detect_products(content)
        keywords = detect_strategy_keywords(content)

        entry = {
            "timestamp": timestamp,
            "author": author,
            "channel": channel,
            "message_id": message.id,
            "content": content[:2000],  # Truncate very long messages
            "code_blocks": code_blocks,
            "products_mentioned": products,
            "strategy_keywords": keywords,
            "has_code": len(code_blocks) > 0,
            "url": message.jump_url,
        }

        # Save to strategies file if it has code
        if code_blocks:
            strategies = load_json(STRATEGIES_FILE)
            # Deduplicate by message_id
            if not any(s.get("message_id") == message.id for s in strategies):
                strategies.append(entry)
                save_json(STRATEGIES_FILE, strategies)
                print(f"[bot] Saved strategy from {author} in #{channel} ({len(code_blocks)} code block(s))")

        # Save to signals file if it mentions products/strategies
        if products or keywords:
            signals = load_json(SIGNALS_FILE)
            if not any(s.get("message_id") == message.id for s in signals):
                signals.append(entry)
                # Keep only last 500 signals to avoid unbounded growth
                if len(signals) > 500:
                    signals = signals[-500:]
                save_json(SIGNALS_FILE, signals)
                print(f"[bot] Saved signal: products={products}, keywords={keywords[:3]}")


class BackfillBot(ProsperityBot):
    """Extended bot that can backfill message history from channels."""

    async def backfill(self, channel_id: int, limit: int = 1000):
        """Scrape the last `limit` messages from a channel."""
        channel = self.get_channel(channel_id)
        if not channel:
            print(f"[backfill] Channel {channel_id} not found")
            return

        print(f"[backfill] Scanning #{channel.name} (last {limit} messages)...")
        count = 0

        async for message in channel.history(limit=limit):
            if message.author.bot:
                continue
            if is_relevant(message.content):
                await self.on_message(message)
                count += 1

        print(f"[backfill] Found {count} relevant messages in #{channel.name}")


def main():
    if not TOKEN:
        print("ERROR: Set DISCORD_TOKEN in .env file", file=sys.stderr)
        print("See .env.example for configuration", file=sys.stderr)
        sys.exit(1)

    # Check for backfill mode
    backfill = "--backfill" in sys.argv
    limit = 1000
    for arg in sys.argv:
        if arg.startswith("--limit="):
            limit = int(arg.split("=")[1])

    if backfill:
        bot = BackfillBot()

        @bot.event
        async def on_ready():
            print(f"[backfill] Logged in as {bot.user}")
            for channel_id in WATCH_CHANNELS:
                await bot.backfill(channel_id, limit=limit)
            print("[backfill] Done. Switching to live monitoring...")

        bot.run(TOKEN)
    else:
        bot = ProsperityBot()
        bot.run(TOKEN)


if __name__ == "__main__":
    main()
