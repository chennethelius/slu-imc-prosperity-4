"""Detect product mentions and auto-discover potential new products."""

from config import KNOWN_PRODUCTS, PRODUCT_BLACKLIST, PRODUCT_PATTERN


def detect_products(content: str) -> list[str]:
    """Detect known product mentions in message content."""
    upper = content.upper()
    return [p for p in KNOWN_PRODUCTS if p in upper]


def discover_potential_products(content: str) -> list[str]:
    """Find ALL_CAPS words that could be new product names."""
    candidates = PRODUCT_PATTERN.findall(content)
    return [
        c for c in candidates
        if c not in KNOWN_PRODUCTS
        and c not in PRODUCT_BLACKLIST
        and len(c) >= 3
        and not c.isdigit()
    ]
