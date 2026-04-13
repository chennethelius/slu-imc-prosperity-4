"""Relevance scoring for Discord messages."""

from config import (
    CHANNEL_MULTIPLIER_DEFAULT,
    CHANNEL_MULTIPLIER_HIGH,
    CHANNEL_MULTIPLIER_LOW,
    CODE_BLOCK_RE,
    HIGH_PRIORITY_CHANNELS,
    LOW_PRIORITY_CHANNELS,
    SCORE_ATTACHMENT,
    SCORE_CODE_BLOCK,
    SCORE_KEYWORD_MAX,
    SCORE_LONG_MESSAGE,
    SCORE_NUMERIC_PARAM,
    SCORE_PRODUCT_MAX,
    SCORE_PRODUCT_MENTION,
    SCORE_REPLY_HIGH,
    SCORE_STRATEGY_KEYWORD,
)
from extractors.parameter_extractor import has_numeric_parameters
from extractors.product_detector import detect_products
from extractors.strategy_classifier import detect_strategy_keywords


def compute_relevance_score(
    content: str,
    channel_id: int,
    has_attachments: bool = False,
    parent_score: int | None = None,
) -> int:
    """Compute relevance score (0-100) for a message.

    Args:
        content: Message text
        channel_id: Channel the message was posted in
        has_attachments: Whether the message has file attachments
        parent_score: Relevance score of the parent message (if reply)
    """
    raw = 0

    # Code blocks
    if CODE_BLOCK_RE.search(content):
        raw += SCORE_CODE_BLOCK

    # Product mentions
    products = detect_products(content)
    raw += min(len(products) * SCORE_PRODUCT_MENTION, SCORE_PRODUCT_MAX)

    # Strategy keywords
    keywords = detect_strategy_keywords(content)
    raw += min(len(keywords) * SCORE_STRATEGY_KEYWORD, SCORE_KEYWORD_MAX)

    # Numeric parameters
    if has_numeric_parameters(content):
        raw += SCORE_NUMERIC_PARAM

    # Message length
    if len(content) > 200:
        raw += SCORE_LONG_MESSAGE

    # Reply to high-relevance message
    if parent_score is not None and parent_score >= 60:
        raw += SCORE_REPLY_HIGH

    # Attachments
    if has_attachments:
        raw += SCORE_ATTACHMENT

    # Channel multiplier
    if channel_id in HIGH_PRIORITY_CHANNELS:
        multiplier = CHANNEL_MULTIPLIER_HIGH
    elif channel_id in LOW_PRIORITY_CHANNELS:
        multiplier = CHANNEL_MULTIPLIER_LOW
    else:
        multiplier = CHANNEL_MULTIPLIER_DEFAULT

    score = min(100, int(raw * multiplier))
    return score
