"""Generate Claude-optimized intel summary from the database."""

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from storage.db import IntelDB


async def generate_claude_intel(db: IntelDB, output_path: Path, current_round: int | None = None):
    """Generate claude_intel.json — structured per-product intel summary."""
    stats = await db.get_stats()
    all_products = await db.get_all_products()

    products_intel = {}
    for product in all_products:
        products_intel[product] = await _build_product_intel(db, product)

    # Top contributors
    top_authors = await db.get_top_authors(limit=15)
    contributors = []
    for a in top_authors:
        if a["message_count"] < 2:
            continue
        contributors.append({
            "name": a["author_name"],
            "credibility": round(a["credibility_score"], 1),
            "messages": a["message_count"],
            "code_messages": a["code_message_count"],
            "avg_relevance": round(a["avg_relevance"], 1),
        })

    # Potential new products
    potential = await db.get_potential_products(min_mentions=3)
    potential_list = [
        {"name": p["name"], "mentions": p["mention_count"]}
        for p in potential
    ]

    intel = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "round": current_round,
        "total_messages_scraped": stats.get("messages", 0),
        "total_parameters_extracted": stats.get("parameters", 0),
        "total_code_blocks": stats.get("code_blocks", 0),
        "products": products_intel,
        "potential_new_products": potential_list,
        "top_contributors": contributors,
    }

    with open(output_path, "w") as f:
        json.dump(intel, f, indent=2, ensure_ascii=False)

    print(f"[export] Wrote claude_intel.json: {len(all_products)} products, "
          f"{stats.get('messages', 0)} messages, {stats.get('parameters', 0)} parameters")

    # Also export all messages for the dashboard
    messages_path = output_path.parent / "messages.json"
    await _export_messages(db, messages_path)

    return intel


async def _export_messages(db: IntelDB, output_path: Path):
    """Export all messages as JSON for the dashboard messages view."""
    cursor = await db._db.execute(
        """SELECT m.message_id, m.timestamp, m.author_name, m.channel_name,
                  m.content, m.relevance_score, m.strategy_type, m.has_code, m.url,
                  GROUP_CONCAT(DISTINCT mp.product) as products
           FROM messages m
           LEFT JOIN message_products mp ON m.message_id = mp.message_id
           GROUP BY m.message_id
           ORDER BY m.relevance_score DESC"""
    )
    rows = await cursor.fetchall()
    messages = []
    for row in rows:
        msg = dict(row)
        msg["products"] = msg["products"].split(",") if msg["products"] else []
        messages.append(msg)

    with open(output_path, "w") as f:
        json.dump(messages, f, indent=2, ensure_ascii=False)
    print(f"[export] Wrote messages.json: {len(messages)} messages")


async def _build_product_intel(db: IntelDB, product: str) -> dict:
    """Build intel summary for a single product."""
    # Parameters with consensus
    raw_params = await db.get_parameters_by_product(product)
    parameters = _aggregate_parameters(raw_params)

    # Strategy consensus
    messages = await db.get_messages_by_product(product, limit=50)
    strategy_consensus = _compute_strategy_consensus(messages)

    # Key observations (high-relevance messages)
    observations = []
    for msg in messages[:10]:
        if msg["relevance_score"] >= 40:
            observations.append({
                "text": _truncate(msg["content"], 200),
                "author": msg["author_name"],
                "score": msg["relevance_score"],
                "timestamp": msg["timestamp"],
            })

    # Code snippets
    code_blocks = await db.get_code_blocks_by_product(product, limit=5)
    snippets = []
    for cb in code_blocks:
        snippets.append({
            "code": _truncate(cb["code"], 500),
            "language": cb["language"],
            "strategy_type": cb["strategy_type"],
            "author": cb["author_name"],
            "relevance": cb["relevance_score"],
        })

    return {
        "strategy_consensus": strategy_consensus,
        "parameters": parameters,
        "key_observations": observations,
        "code_snippets": snippets,
        "message_count": len(messages),
    }


def _aggregate_parameters(raw_params: list[dict]) -> dict:
    """Aggregate parameter values across sources, computing consensus."""
    by_name: dict[str, list[dict]] = defaultdict(list)
    for p in raw_params:
        by_name[p["param_name"]].append(p)

    result = {}
    for param_name, entries in by_name.items():
        # Find the most common value (consensus)
        value_counts: dict[float, int] = defaultdict(int)
        for e in entries:
            value_counts[e["param_value"]] += e["source_count"]

        if not value_counts:
            continue

        best_value = max(value_counts, key=value_counts.get)
        total_sources = sum(value_counts.values())
        best_confidence = max(e["confidence"] for e in entries if e["param_value"] == best_value)

        # Boost confidence based on consensus
        consensus_ratio = value_counts[best_value] / total_sources if total_sources > 0 else 0
        adjusted_confidence = min(1.0, best_confidence * (0.7 + 0.3 * consensus_ratio))

        result[param_name] = {
            "value": best_value,
            "confidence": round(adjusted_confidence, 2),
            "sources": total_sources,
            "all_values": [
                {"value": v, "count": c}
                for v, c in sorted(value_counts.items(), key=lambda x: -x[1])
            ],
        }

    return result


def _compute_strategy_consensus(messages: list[dict]) -> str | None:
    """Determine the dominant strategy type from messages about a product."""
    type_counts: dict[str, int] = defaultdict(int)
    for msg in messages:
        if msg.get("strategy_type"):
            type_counts[msg["strategy_type"]] += 1
    if not type_counts:
        return None
    return max(type_counts, key=type_counts.get)


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."
