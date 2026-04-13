"""Async SQLite storage layer for the Discord intel bot."""

import json
from pathlib import Path

import aiosqlite

from storage.models import AuthorRecord, ExtractedCode, ExtractedParameter, MessageRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    author_id INTEGER NOT NULL,
    author_name TEXT NOT NULL,
    channel_id INTEGER NOT NULL,
    channel_name TEXT NOT NULL,
    thread_id INTEGER,
    round INTEGER,
    content TEXT NOT NULL,
    relevance_score INTEGER DEFAULT 0,
    strategy_type TEXT,
    has_code BOOLEAN DEFAULT 0,
    url TEXT,
    parent_message_id INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS message_products (
    message_id INTEGER REFERENCES messages(message_id),
    product TEXT NOT NULL,
    PRIMARY KEY (message_id, product)
);

CREATE TABLE IF NOT EXISTS code_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER REFERENCES messages(message_id),
    language TEXT DEFAULT 'python',
    code TEXT NOT NULL,
    imports TEXT
);

CREATE TABLE IF NOT EXISTS parameters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER REFERENCES messages(message_id),
    product TEXT NOT NULL,
    param_name TEXT NOT NULL,
    param_value REAL NOT NULL,
    confidence REAL DEFAULT 0.5,
    round INTEGER
);

CREATE TABLE IF NOT EXISTS authors (
    author_id INTEGER PRIMARY KEY,
    author_name TEXT NOT NULL,
    message_count INTEGER DEFAULT 0,
    code_message_count INTEGER DEFAULT 0,
    avg_relevance REAL DEFAULT 0,
    credibility_score REAL DEFAULT 2.0,
    first_seen TEXT,
    last_seen TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS message_keywords (
    message_id INTEGER REFERENCES messages(message_id),
    keyword TEXT NOT NULL,
    PRIMARY KEY (message_id, keyword)
);

CREATE TABLE IF NOT EXISTS potential_products (
    name TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    mention_count INTEGER DEFAULT 1,
    confirmed BOOLEAN DEFAULT 0,
    sample_message_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_messages_round ON messages(round);
CREATE INDEX IF NOT EXISTS idx_messages_relevance ON messages(relevance_score DESC);
CREATE INDEX IF NOT EXISTS idx_messages_author ON messages(author_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_mp_product ON message_products(product);
CREATE INDEX IF NOT EXISTS idx_params_product ON parameters(product);
CREATE INDEX IF NOT EXISTS idx_params_name ON parameters(param_name);
"""


class IntelDB:
    """Async SQLite database for storing scraped Discord intel."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self):
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        return self

    async def close(self):
        if self._db:
            await self._db.close()

    async def __aenter__(self):
        return await self.connect()

    async def __aexit__(self, *args):
        await self.close()

    # --- Message CRUD ---

    async def message_exists(self, message_id: int) -> bool:
        cursor = await self._db.execute(
            "SELECT 1 FROM messages WHERE message_id = ?", (message_id,)
        )
        return await cursor.fetchone() is not None

    async def insert_message(self, record: MessageRecord):
        """Insert a full message record with all extracted data."""
        if await self.message_exists(record.message_id):
            return

        await self._db.execute(
            """INSERT INTO messages
               (message_id, timestamp, author_id, author_name, channel_id,
                channel_name, thread_id, round, content, relevance_score,
                strategy_type, has_code, url, parent_message_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.message_id, record.timestamp, record.author_id,
                record.author_name, record.channel_id, record.channel_name,
                record.thread_id, record.round, record.content[:4000],
                record.relevance_score, record.strategy_type,
                record.has_code, record.url, record.parent_message_id,
            ),
        )

        # Products
        for product in record.products:
            await self._db.execute(
                "INSERT OR IGNORE INTO message_products (message_id, product) VALUES (?, ?)",
                (record.message_id, product),
            )

        # Keywords
        for keyword in record.keywords:
            await self._db.execute(
                "INSERT OR IGNORE INTO message_keywords (message_id, keyword) VALUES (?, ?)",
                (record.message_id, keyword),
            )

        # Code blocks
        for cb in record.code_blocks:
            await self._db.execute(
                "INSERT INTO code_blocks (message_id, language, code, imports) VALUES (?, ?, ?, ?)",
                (record.message_id, cb.language, cb.code, json.dumps(cb.imports)),
            )

        # Parameters
        for param in record.parameters:
            await self._db.execute(
                """INSERT INTO parameters
                   (message_id, product, param_name, param_value, confidence, round)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record.message_id, param.product, param.param_name,
                    param.param_value, param.confidence, param.round,
                ),
            )

        # Potential new products
        for name in record.potential_products:
            await self._db.execute(
                """INSERT INTO potential_products (name, first_seen, mention_count, sample_message_id)
                   VALUES (?, ?, 1, ?)
                   ON CONFLICT(name) DO UPDATE SET mention_count = mention_count + 1""",
                (name, record.timestamp, record.message_id),
            )

        # Update author stats
        await self._update_author(record)

        await self._db.commit()

    async def _update_author(self, record: MessageRecord):
        """Upsert author stats."""
        existing = await self._db.execute(
            "SELECT * FROM authors WHERE author_id = ?", (record.author_id,)
        )
        row = await existing.fetchone()

        if row is None:
            await self._db.execute(
                """INSERT INTO authors
                   (author_id, author_name, message_count, code_message_count,
                    avg_relevance, first_seen, last_seen)
                   VALUES (?, ?, 1, ?, ?, ?, ?)""",
                (
                    record.author_id, record.author_name,
                    1 if record.has_code else 0,
                    float(record.relevance_score),
                    record.timestamp, record.timestamp,
                ),
            )
        else:
            new_count = row["message_count"] + 1
            new_code = row["code_message_count"] + (1 if record.has_code else 0)
            new_avg = (row["avg_relevance"] * row["message_count"] + record.relevance_score) / new_count
            await self._db.execute(
                """UPDATE authors SET
                   author_name = ?, message_count = ?, code_message_count = ?,
                   avg_relevance = ?, last_seen = ?
                   WHERE author_id = ?""",
                (record.author_name, new_count, new_code, new_avg, record.timestamp, record.author_id),
            )

    # --- Query Methods ---

    async def get_messages_by_product(self, product: str, limit: int = 50) -> list[dict]:
        cursor = await self._db.execute(
            """SELECT m.* FROM messages m
               JOIN message_products mp ON m.message_id = mp.message_id
               WHERE mp.product = ?
               ORDER BY m.relevance_score DESC
               LIMIT ?""",
            (product, limit),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_parameters_by_product(self, product: str) -> list[dict]:
        cursor = await self._db.execute(
            """SELECT param_name, param_value, confidence, round,
                      COUNT(*) as source_count
               FROM parameters
               WHERE product = ?
               GROUP BY param_name, param_value
               ORDER BY source_count DESC, confidence DESC""",
            (product,),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_code_blocks_by_product(self, product: str, limit: int = 10) -> list[dict]:
        cursor = await self._db.execute(
            """SELECT cb.*, m.relevance_score, m.author_name, m.strategy_type
               FROM code_blocks cb
               JOIN messages m ON cb.message_id = m.message_id
               JOIN message_products mp ON m.message_id = mp.message_id
               WHERE mp.product = ?
               ORDER BY m.relevance_score DESC
               LIMIT ?""",
            (product, limit),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_top_authors(self, limit: int = 20) -> list[dict]:
        cursor = await self._db.execute(
            """SELECT * FROM authors
               ORDER BY credibility_score DESC, avg_relevance DESC
               LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_potential_products(self, min_mentions: int = 3) -> list[dict]:
        cursor = await self._db.execute(
            """SELECT * FROM potential_products
               WHERE confirmed = 0 AND mention_count >= ?
               ORDER BY mention_count DESC""",
            (min_mentions,),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_all_products(self) -> list[str]:
        cursor = await self._db.execute(
            "SELECT DISTINCT product FROM message_products ORDER BY product"
        )
        return [row["product"] for row in await cursor.fetchall()]

    async def get_high_relevance_messages(self, min_score: int = 60, limit: int = 100) -> list[dict]:
        cursor = await self._db.execute(
            """SELECT m.*, GROUP_CONCAT(DISTINCT mp.product) as products
               FROM messages m
               LEFT JOIN message_products mp ON m.message_id = mp.message_id
               WHERE m.relevance_score >= ?
               GROUP BY m.message_id
               ORDER BY m.relevance_score DESC
               LIMIT ?""",
            (min_score, limit),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_message_score(self, message_id: int) -> int | None:
        """Get the relevance score of a message (for reply scoring)."""
        cursor = await self._db.execute(
            "SELECT relevance_score FROM messages WHERE message_id = ?",
            (message_id,),
        )
        row = await cursor.fetchone()
        return row["relevance_score"] if row else None

    async def get_stats(self) -> dict:
        stats = {}
        for table in ["messages", "code_blocks", "parameters", "authors"]:
            cursor = await self._db.execute(f"SELECT COUNT(*) as cnt FROM {table}")
            row = await cursor.fetchone()
            stats[table] = row["cnt"]

        cursor = await self._db.execute(
            "SELECT COUNT(DISTINCT product) as cnt FROM message_products"
        )
        row = await cursor.fetchone()
        stats["unique_products"] = row["cnt"]

        cursor = await self._db.execute(
            "SELECT AVG(relevance_score) as avg_score FROM messages"
        )
        row = await cursor.fetchone()
        stats["avg_relevance"] = round(row["avg_score"] or 0, 1)

        return stats
