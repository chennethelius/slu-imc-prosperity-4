"""Generate Claude-optimized intel summary from the database."""

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from storage.db import IntelDB


# Match profit/PnL values in messages: 7500, 10k, 2.3k, ~3200, $5000, -500
_PNL_RE = re.compile(
    r"(?<![\w.])(-?\$?\d{1,3}(?:,\d{3})+|-?\$?\d+(?:\.\d+)?)(k|K)?(?!\w)"
)


def _extract_pnl_mentions(text: str) -> list[float]:
    """Parse numeric profit mentions from a message. Returns list of values."""
    if not text:
        return []
    values = []
    for match in _PNL_RE.finditer(text):
        num_str, suffix = match.group(1), match.group(2)
        num_str = num_str.replace(",", "").replace("$", "")
        try:
            val = float(num_str)
            if suffix:
                val *= 1000
            # Filter: likely PnL values (not timestamps, prices under 100, tiny decimals)
            if 100 <= abs(val) <= 500_000:
                values.append(val)
        except ValueError:
            continue
    return values


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
        pnls = _extract_pnl_mentions(msg["content"])
        msg["pnl_mentions"] = pnls
        msg["max_pnl"] = max(pnls) if pnls else None
        messages.append(msg)

    with open(output_path, "w") as f:
        json.dump(messages, f, indent=2, ensure_ascii=False)
    print(f"[export] Wrote messages.json: {len(messages)} messages")

    # Build auto-digest
    digest = _build_digest(messages)
    digest_path = output_path.parent / "digest.json"
    with open(digest_path, "w") as f:
        json.dump(digest, f, indent=2, ensure_ascii=False)
    print(f"[export] Wrote digest.json")


def _build_digest(messages: list[dict]) -> dict:
    """Extractive digest — picks top insights without needing an LLM."""
    from collections import Counter

    # Group by product
    by_product = defaultdict(list)
    for m in messages:
        for p in m.get("products", []):
            if p:
                by_product[p].append(m)

    products_digest = {}
    for product, msgs in by_product.items():
        # Top 5 highest-scored messages
        top = sorted(msgs, key=lambda x: -x["relevance_score"])[:5]
        # PnL distribution
        all_pnls = []
        for m in msgs:
            all_pnls.extend(m.get("pnl_mentions") or [])
        pnl_stats = None
        if all_pnls:
            pnl_stats = {
                "min": min(all_pnls),
                "max": max(all_pnls),
                "median": sorted(all_pnls)[len(all_pnls) // 2],
                "common": [v for v, _ in Counter([round(x / 500) * 500 for x in all_pnls]).most_common(3)],
                "count": len(all_pnls),
            }
        # Most active authors
        authors = Counter([m["author_name"] for m in msgs]).most_common(5)
        products_digest[product] = {
            "message_count": len(msgs),
            "top_messages": [
                {
                    "score": m["relevance_score"],
                    "author": m["author_name"],
                    "content": m["content"][:400],
                    "url": m.get("url"),
                    "pnls": m.get("pnl_mentions") or [],
                }
                for m in top
            ],
            "pnl_stats": pnl_stats,
            "top_contributors": [{"name": a, "messages": c} for a, c in authors],
        }

    # Overall top messages (any product)
    overall_top = sorted(messages, key=lambda x: -x["relevance_score"])[:10]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_messages": len(messages),
        "products": products_digest,
        "overall_top_messages": [
            {
                "score": m["relevance_score"],
                "author": m["author_name"],
                "channel": m["channel_name"],
                "content": m["content"][:400],
                "products": m.get("products", []),
                "url": m.get("url"),
                "pnls": m.get("pnl_mentions") or [],
            }
            for m in overall_top
        ],
    }


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
