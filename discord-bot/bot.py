#!/usr/bin/env python3
"""
Discord intel bot for IMC Prosperity 4.

Silently scrapes the IMC Prosperity Discord for strategy discussions,
code snippets, and trading parameters. Stores structured intel in SQLite
and exports Claude-optimized summaries.

Usage:
    python bot.py                         # Live monitoring
    python bot.py --backfill              # Backfill history then monitor
    python bot.py --backfill --limit=500  # Backfill with custom limit
    python bot.py --export                # Just regenerate claude_intel.json

Setup:
    1. Copy .env.example to .env
    2. Add your Discord user token (from browser DevTools)
    3. Add channel IDs to monitor
    4. pip install -r requirements.txt
    5. python bot.py
"""

import asyncio
import sys

try:
    import discord
    from dotenv import load_dotenv
except ImportError:
    print("ERROR: Install dependencies first: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

load_dotenv()

from config import CLAUDE_EXPORT_PATH, CURRENT_ROUND, DB_PATH, TOKEN, WATCH_CHANNELS
from export.claude_export import generate_claude_intel
from scraper.backfiller import backfill_all_channels
from scraper.listener import process_message
from storage.db import IntelDB


class ProsperityIntelBot(discord.Client):
    """Silent selfbot that scrapes and processes messages."""

    def __init__(self, db: IntelDB, do_backfill: bool = False, backfill_limit: int | None = None):
        super().__init__()
        self.db = db
        self.do_backfill = do_backfill
        self.backfill_limit = backfill_limit
        self._message_count = 0

    async def on_ready(self):
        print(f"[bot] Logged in as {self.user}")
        print(f"[bot] Watching {len(WATCH_CHANNELS)} channel(s): {WATCH_CHANNELS}")
        print(f"[bot] Database: {DB_PATH}")
        print(f"[bot] Current round: {CURRENT_ROUND or 'tutorial'}")

        if self.do_backfill:
            print("[bot] Starting backfill...")
            await backfill_all_channels(self, self.db, self.backfill_limit)

            # Generate export after backfill
            await generate_claude_intel(self.db, CLAUDE_EXPORT_PATH, CURRENT_ROUND or None)
            print("[bot] Backfill complete. Switching to live monitoring...")

        stats = await self.db.get_stats()
        print(f"[bot] DB stats: {stats}")
        print("[bot] Listening for new messages...")

    async def on_message(self, message: discord.Message):
        record = await process_message(message, self.db)
        if record:
            self._message_count += 1
            products = ", ".join(record.products) if record.products else "none"
            params = len(record.parameters)
            print(
                f"[bot] [{record.relevance_score:3d}] "
                f"#{record.channel_name} | {record.author_name} | "
                f"products={products} | params={params} | "
                f"code={'yes' if record.has_code else 'no'}"
            )

            # Auto-export on high-relevance messages
            if record.relevance_score >= 80:
                await generate_claude_intel(self.db, CLAUDE_EXPORT_PATH, CURRENT_ROUND or None)

            # Periodic export every 50 messages
            if self._message_count % 50 == 0:
                await generate_claude_intel(self.db, CLAUDE_EXPORT_PATH, CURRENT_ROUND or None)


async def run_export_only():
    """Just regenerate the Claude export from existing DB."""
    async with IntelDB(DB_PATH) as db:
        stats = await db.get_stats()
        print(f"[export] DB stats: {stats}")
        await generate_claude_intel(db, CLAUDE_EXPORT_PATH, CURRENT_ROUND or None)


def main():
    if not TOKEN:
        print("ERROR: Set DISCORD_TOKEN in .env file", file=sys.stderr)
        print("See .env.example for configuration", file=sys.stderr)
        sys.exit(1)

    # Parse args
    args = sys.argv[1:]
    do_backfill = "--backfill" in args
    export_only = "--export" in args
    limit = None
    for arg in args:
        if arg.startswith("--limit="):
            limit = int(arg.split("=")[1])

    if export_only:
        asyncio.run(run_export_only())
        return

    # Main bot loop
    async def start():
        db = IntelDB(DB_PATH)
        await db.connect()
        try:
            bot = ProsperityIntelBot(db, do_backfill=do_backfill, backfill_limit=limit)
            await bot.start(TOKEN)
        finally:
            await db.close()

    asyncio.run(start())


if __name__ == "__main__":
    main()
