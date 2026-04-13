"""Extract structured trading parameters from message content."""

import re

from config import KNOWN_PRODUCTS
from storage.models import ExtractedParameter

# Each pattern: (regex, param_name, base_confidence)
# {product} placeholder is expanded for each known product
PARAMETER_PATTERNS = [
    # "fair value is 2800" / "fv = 2800" / "fair price around 10000"
    (r"(?:fair\s*value|fv|fair\s*price)\s*(?:is|=|:|of|for|around|~|about)?\s*~?\s*(\d+(?:\.\d+)?)",
     "fair_value", 0.8),

    # "EMA alpha 0.03" / "alpha=0.027" / "alpha of 0.03"
    (r"(?:ema\s*)?alpha\s*(?:is|=|:|of)?\s*(\d+\.\d+)",
     "ema_alpha", 0.7),

    # "spread of 2" / "spread=3" / "spread is 1.5"
    (r"(?:bid.?ask\s*)?spread\s*(?:is|=|:|of)?\s*(\d+(?:\.\d+)?)",
     "spread", 0.6),

    # "window 50" / "lookback 100" / "period of 200"
    (r"(?:window|lookback|period)\s*(?:is|=|:|of)?\s*(\d+)",
     "window_size", 0.6),

    # "strike 10000" / "strike price 9500"
    (r"strike\s*(?:price)?\s*(?:is|=|:|of)?\s*(\d+)",
     "strike_price", 0.8),

    # "volatility 0.15" / "vol=20%" / "sigma 0.2"
    (r"(?:vol(?:atility)?|sigma|implied\s*vol)\s*(?:is|=|:|of)?\s*(\d+(?:\.\d+)?)\s*%?",
     "volatility", 0.6),

    # "position limit 50" / "limit is 80"
    (r"(?:position\s*)?limit\s*(?:is|=|:|of)?\s*(\d+)",
     "position_limit", 0.5),

    # "transport fee 1.5" / "tariff 0.15"
    (r"(?:transport\s*(?:fee|cost)|(?:export|import)\s*tariff)\s*(?:is|=|:|of)?\s*(\d+(?:\.\d+)?)",
     "transport_cost", 0.7),

    # "edge of 2" / "edge=1.5"
    (r"edge\s*(?:is|=|:|of)?\s*(\d+(?:\.\d+)?)",
     "edge", 0.5),
]

# Basket weight: "6 CROISSANTS" or "6x CROISSANTS"
BASKET_WEIGHT_RE = re.compile(
    r"(\d+)\s*(?:x\s*)?(" + "|".join(re.escape(p) for p in KNOWN_PRODUCTS) + r")",
    re.IGNORECASE,
)


def extract_parameters(content: str, products: list[str], current_round: int | None = None) -> list[ExtractedParameter]:
    """Extract structured parameters from message content.

    Args:
        content: Raw message text
        products: Products mentioned in this message (for association)
        current_round: Current competition round number
    """
    results = []
    lower = content.lower()

    for pattern, param_name, confidence in PARAMETER_PATTERNS:
        matches = re.finditer(pattern, lower)
        for match in matches:
            value_str = match.group(1)
            try:
                value = float(value_str)
            except ValueError:
                continue

            # Associate parameter with mentioned products
            # If no products mentioned, store with product="UNKNOWN"
            target_products = products if products else ["UNKNOWN"]
            for product in target_products:
                results.append(ExtractedParameter(
                    product=product,
                    param_name=param_name,
                    param_value=value,
                    confidence=confidence,
                    round=current_round,
                ))

    # Basket weights: "6 CROISSANTS + 3 JAMS + 1 DJEMBE"
    for match in BASKET_WEIGHT_RE.finditer(content):
        weight = int(match.group(1))
        product = match.group(2).upper()
        results.append(ExtractedParameter(
            product=product,
            param_name="basket_weight",
            param_value=float(weight),
            confidence=0.7,
            round=current_round,
        ))

    return results


def has_numeric_parameters(content: str) -> bool:
    """Quick check: does the message contain parameter-like patterns?"""
    lower = content.lower()
    for pattern, _, _ in PARAMETER_PATTERNS:
        if re.search(pattern, lower):
            return True
    return bool(BASKET_WEIGHT_RE.search(content))
