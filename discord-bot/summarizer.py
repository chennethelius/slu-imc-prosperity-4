"""Strategy summarizer — groups scraped messages by product/strategy and generates briefs via Gemini."""

import asyncio
import json
import os
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

from config import CLAUDE_EXPORT_PATH, CURRENT_ROUND, DB_PATH, STORAGE_DIR
from storage.db import IntelDB

load_dotenv()

GCLOUD_PROJECT = os.getenv("GCLOUD_PROJECT", "")
GCLOUD_LOCATION = os.getenv("GCLOUD_LOCATION", "us-central1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-001")
# Optional: direct API key (uses google.generativeai instead of Vertex)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

SYSTEM_PROMPT = """You are an expert algorithmic trading analyst reviewing community discussions about IMC Prosperity 4, an algorithmic trading competition.

Your job is to synthesize Discord messages into actionable strategy briefs for a trading team. Be specific and quantitative — include exact parameter values, price levels, and formulas when mentioned.

For each product, output:
1. **Consensus Strategy** — what approach most people are using and why
2. **Key Parameters** — specific values mentioned (fair value, spread, EMA alpha, etc.)
3. **Edge Opportunities** — non-obvious insights or contrarian ideas worth testing
4. **Risks & Pitfalls** — common mistakes or things that don't work
5. **Code Patterns** — any useful code snippets or algorithmic approaches shared

Be concise. Skip generic advice. Focus on actionable intel that would help write a better trading algorithm."""


async def load_messages_by_product(db: IntelDB) -> dict[str, list[dict]]:
    """Group all messages by product, sorted by relevance."""
    products = await db.get_all_products()
    result = {}
    for product in products:
        msgs = await db.get_messages_by_product(product, limit=100)
        if msgs:
            result[product] = msgs
    return result


async def load_all_messages(db: IntelDB) -> list[dict]:
    """Load all messages for a general overview."""
    cursor = await db._db.execute(
        """SELECT m.*, GROUP_CONCAT(DISTINCT mp.product) as products
           FROM messages m
           LEFT JOIN message_products mp ON m.message_id = mp.message_id
           GROUP BY m.message_id
           ORDER BY m.relevance_score DESC
           LIMIT 200"""
    )
    return [dict(row) for row in await cursor.fetchall()]


def format_messages_for_llm(messages: list[dict], max_chars: int = 12000) -> str:
    """Format messages into a readable block for the LLM prompt."""
    lines = []
    total = 0
    for msg in messages:
        line = f"[score:{msg['relevance_score']}] {msg['author_name']}: {msg['content'][:300]}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n\n".join(lines)


def _get_model():
    """Initialize Gemini model — Vertex AI (GCloud) or direct API key."""
    if GEMINI_API_KEY:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        return genai.GenerativeModel(GEMINI_MODEL, system_instruction=SYSTEM_PROMPT)
    else:
        from google.cloud import aiplatform
        from vertexai.generative_models import GenerativeModel
        project = GCLOUD_PROJECT or os.popen("gcloud config get-value project 2>/dev/null").read().strip()
        aiplatform.init(project=project, location=GCLOUD_LOCATION)
        return GenerativeModel(GEMINI_MODEL, system_instruction=SYSTEM_PROMPT)


async def summarize_product(product: str, messages: list[dict]) -> str:
    """Generate a strategy brief for a single product."""
    formatted = format_messages_for_llm(messages)
    prompt = f"""Analyze these {len(messages)} Discord messages about **{product}** from the IMC Prosperity 4 competition.

{formatted}

Generate a strategy brief for {product}. Be specific and quantitative."""

    model = _get_model()
    response = model.generate_content(prompt)
    return response.text


async def summarize_overview(messages: list[dict]) -> str:
    """Generate a high-level overview across all products."""
    formatted = format_messages_for_llm(messages, max_chars=15000)
    prompt = f"""Analyze these {len(messages)} top Discord messages from the IMC Prosperity 4 competition.

{formatted}

Generate:
1. **Meta Overview** — what round/phase are people focused on, general sentiment
2. **Product Rankings** — which products are most discussed and why
3. **Cross-Product Strategies** — basket arb, correlation plays, portfolio-level ideas
4. **Community Consensus vs Contrarian** — what everyone agrees on vs minority opinions worth exploring
5. **Actionable Next Steps** — top 3 things to try in our next strategy iteration"""

    model = _get_model()
    response = model.generate_content(prompt)
    return response.text


async def generate_briefs(db: IntelDB) -> dict:
    """Generate all strategy briefs and save to disk."""
    by_product = await load_messages_by_product(db)
    all_msgs = await load_all_messages(db)

    briefs = {"overview": None, "products": {}, "generated_at": None}

    # Overview
    print("[summarizer] Generating overview brief...")
    briefs["overview"] = await summarize_overview(all_msgs)

    # Per-product briefs
    for product, messages in by_product.items():
        if len(messages) < 3:
            print(f"[summarizer] Skipping {product} — only {len(messages)} messages")
            continue
        print(f"[summarizer] Generating brief for {product} ({len(messages)} messages)...")
        briefs["products"][product] = await summarize_product(product, messages)

    from datetime import datetime, timezone
    briefs["generated_at"] = datetime.now(timezone.utc).isoformat()

    # Save
    output_path = STORAGE_DIR / "strategy_briefs.json"
    with open(output_path, "w") as f:
        json.dump(briefs, f, indent=2, ensure_ascii=False)
    print(f"[summarizer] Saved to {output_path}")

    # Also save as readable markdown
    md_path = STORAGE_DIR / "strategy_briefs.md"
    with open(md_path, "w") as f:
        f.write("# Strategy Briefs\n\n")
        f.write(f"*Generated: {briefs['generated_at']}*\n\n")
        if briefs["overview"]:
            f.write("## Overview\n\n")
            f.write(briefs["overview"] + "\n\n---\n\n")
        for product, brief in briefs["products"].items():
            f.write(f"## {product}\n\n")
            f.write(brief + "\n\n---\n\n")
    print(f"[summarizer] Saved markdown to {md_path}")

    return briefs


async def query_messages(db: IntelDB, query: str, limit: int = 20) -> list[dict]:
    """Simple keyword search across messages. Returns most relevant matches."""
    terms = query.lower().split()
    # Build SQL with LIKE for each term
    conditions = " AND ".join([f"LOWER(m.content) LIKE '%' || ? || '%'" for _ in terms])
    sql = f"""SELECT m.*, GROUP_CONCAT(DISTINCT mp.product) as products
              FROM messages m
              LEFT JOIN message_products mp ON m.message_id = mp.message_id
              WHERE {conditions}
              GROUP BY m.message_id
              ORDER BY m.relevance_score DESC
              LIMIT ?"""
    cursor = await db._db.execute(sql, [*terms, limit])
    return [dict(row) for row in await cursor.fetchall()]


async def query_and_synthesize(db: IntelDB, query: str) -> str:
    """Search messages and synthesize an answer using Gemini."""
    messages = await query_messages(db, query)
    if not messages:
        return f"No messages found matching '{query}'."

    formatted = format_messages_for_llm(messages)
    prompt = f"""Based on these Discord messages from IMC Prosperity 4, answer this question:

**{query}**

Messages:
{formatted}

Give a direct, actionable answer based on what the community has discussed. Include specific values and parameters when available."""

    model = _get_model()
    response = model.generate_content(prompt)

    return f"**Query:** {query}\n**Matched:** {len(messages)} messages\n\n{response.text}"


async def main():
    import sys
    args = sys.argv[1:]

    if not GEMINI_API_KEY and not GCLOUD_PROJECT:
        # Try to detect gcloud project
        project = os.popen("gcloud config get-value project 2>/dev/null").read().strip()
        if not project:
            print("ERROR: Set GEMINI_API_KEY or GCLOUD_PROJECT in .env", file=sys.stderr)
            sys.exit(1)
        print(f"[summarizer] Using Vertex AI with project: {project}")

    async with IntelDB(DB_PATH) as db:
        if "--query" in args:
            idx = args.index("--query")
            query = " ".join(args[idx + 1:])
            if not query:
                print("Usage: python summarizer.py --query <your question>")
                return
            result = await query_and_synthesize(db, query)
            print(result)
        elif "--search" in args:
            idx = args.index("--search")
            query = " ".join(args[idx + 1:])
            messages = await query_messages(db, query)
            for m in messages:
                prods = m.get("products", "") or ""
                print(f"[{m['relevance_score']:3d}] {m['author_name']} | {prods} | {m['content'][:120]}")
        else:
            # Default: generate all briefs
            briefs = await generate_briefs(db)
            print(f"\nDone. {len(briefs['products'])} product briefs generated.")


if __name__ == "__main__":
    asyncio.run(main())
