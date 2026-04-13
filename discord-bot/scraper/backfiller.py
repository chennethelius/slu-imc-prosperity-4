"""Backfill channel history — scrape past messages from Discord channels."""

import asyncio

import discord

from config import BACKFILL_LIMIT, WATCH_CHANNELS
from scraper.listener import process_message
from storage.db import IntelDB


async def backfill_channel(
    client: discord.Client,
    channel_id: int,
    db: IntelDB,
    limit: int | None = None,
) -> int:
    """Scrape the last `limit` messages from a channel.

    Returns the number of relevant messages stored.
    """
    limit = limit or BACKFILL_LIMIT
    channel = client.get_channel(channel_id)
    if not channel:
        print(f"[backfill] Channel {channel_id} not found")
        return 0

    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        print(f"[backfill] Channel {channel_id} is not a text channel")
        return 0

    print(f"[backfill] Scanning #{channel.name} (last {limit} messages)...")
    count = 0
    processed = 0

    async for message in channel.history(limit=limit):
        processed += 1
        record = await process_message(message, db)
        if record:
            count += 1

        # Progress indicator every 200 messages
        if processed % 200 == 0:
            print(f"[backfill]   ...processed {processed} messages, {count} relevant")

    print(f"[backfill] #{channel.name}: {count} relevant out of {processed} processed")
    return count


async def backfill_all_channels(
    client: discord.Client,
    db: IntelDB,
    limit: int | None = None,
) -> dict[str, int]:
    """Backfill all watched channels.

    Returns a dict mapping channel name -> count of relevant messages.
    """
    results = {}
    for channel_id in WATCH_CHANNELS:
        count = await backfill_channel(client, channel_id, db, limit)
        channel = client.get_channel(channel_id)
        name = str(channel) if channel else str(channel_id)
        results[name] = count
        # Rate limit courtesy — pause between channels
        await asyncio.sleep(1)

    total = sum(results.values())
    print(f"[backfill] Done. {total} relevant messages across {len(results)} channels.")
    return results
