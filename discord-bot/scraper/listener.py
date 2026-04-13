"""Real-time message listener — processes incoming Discord messages."""

import discord

from config import (
    CURRENT_ROUND,
    MIN_MESSAGE_LENGTH,
    RELEVANCE_THRESHOLD,
    WATCH_CHANNELS,
)
from extractors.code_extractor import extract_code_blocks
from extractors.parameter_extractor import extract_parameters
from extractors.product_detector import detect_products, discover_potential_products
from extractors.strategy_classifier import classify_strategy, detect_strategy_keywords
from scoring.relevance import compute_relevance_score
from storage.db import IntelDB
from storage.models import MessageRecord


async def process_message(message: discord.Message, db: IntelDB) -> MessageRecord | None:
    """Process a single Discord message through the full pipeline.

    Returns the MessageRecord if it was stored, None if discarded.
    """
    # Basic filters
    if message.author.bot:
        return None
    if WATCH_CHANNELS and message.channel.id not in WATCH_CHANNELS:
        return None

    content = message.content
    if len(content) < MIN_MESSAGE_LENGTH:
        return None

    # Already scraped?
    if await db.message_exists(message.id):
        return None

    # Get parent score if this is a reply
    parent_score = None
    parent_id = None
    if message.reference and message.reference.message_id:
        parent_id = message.reference.message_id
        parent_score = await db.get_message_score(parent_id)

    # Score the message
    score = compute_relevance_score(
        content=content,
        channel_id=message.channel.id,
        has_attachments=len(message.attachments) > 0,
        parent_score=parent_score,
    )

    if score < RELEVANCE_THRESHOLD:
        return None

    # Run extractors
    products = detect_products(content)
    keywords = detect_strategy_keywords(content)
    code_blocks = extract_code_blocks(content)
    parameters = extract_parameters(content, products, CURRENT_ROUND or None)
    strategy_type = classify_strategy(content)
    potential_products = discover_potential_products(content) if score >= 40 else []

    # Determine thread context
    thread_id = None
    if isinstance(message.channel, discord.Thread):
        thread_id = message.channel.id

    # Build record
    record = MessageRecord(
        message_id=message.id,
        timestamp=message.created_at.isoformat(),
        author_id=message.author.id,
        author_name=str(message.author),
        channel_id=message.channel.id,
        channel_name=str(message.channel),
        content=content[:4000],
        relevance_score=score,
        thread_id=thread_id,
        round=CURRENT_ROUND or None,
        strategy_type=strategy_type,
        has_code=len(code_blocks) > 0,
        url=message.jump_url,
        parent_message_id=parent_id,
        products=products,
        keywords=keywords,
        code_blocks=code_blocks,
        parameters=parameters,
        potential_products=potential_products,
    )

    # Store
    await db.insert_message(record)

    return record
