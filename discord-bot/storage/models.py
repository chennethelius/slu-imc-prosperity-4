"""Data models for the Discord intel bot."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ExtractedCode:
    language: str
    code: str
    imports: list[str] = field(default_factory=list)


@dataclass
class ExtractedParameter:
    product: str
    param_name: str  # fair_value, ema_alpha, spread, window_size, etc.
    param_value: float
    confidence: float = 0.5
    round: Optional[int] = None


@dataclass
class MessageRecord:
    """Structured record for a scraped Discord message."""
    message_id: int
    timestamp: str
    author_id: int
    author_name: str
    channel_id: int
    channel_name: str
    content: str
    relevance_score: int = 0
    thread_id: Optional[int] = None
    round: Optional[int] = None
    strategy_type: Optional[str] = None
    has_code: bool = False
    url: Optional[str] = None
    parent_message_id: Optional[int] = None

    # Extracted data (not stored directly in messages table)
    products: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    code_blocks: list[ExtractedCode] = field(default_factory=list)
    parameters: list[ExtractedParameter] = field(default_factory=list)
    potential_products: list[str] = field(default_factory=list)


@dataclass
class AuthorRecord:
    author_id: int
    author_name: str
    message_count: int = 0
    code_message_count: int = 0
    avg_relevance: float = 0.0
    credibility_score: float = 2.0
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    notes: Optional[str] = None
