import aiosqlite
from datetime import datetime
from typing import Optional

DB_PATH = "bot.db"


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                telegram_id INTEGER PRIMARY KEY,
                expires_at TEXT,
                FOREIGN KEY (telegram_id) REFERENCES users (telegram_id)
            )
        """)
        await db.commit()


async def ensure_user(telegram_id: int, username: Optional[str]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT telegram_id FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cur.fetchone()
        if row is None:
            await db.execute(
                "INSERT INTO users (telegram_id, username, created_at) VALUES (?, ?, ?)",
                (telegram_id, username, datetime.utcnow().isoformat())
            )
            await db.commit()


async def get_subscription_expires_at(telegram_id: int) -> Optional[datetime]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT expires_at FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cur.fetchone()

    if not row or not row[0]:
        return None

    try:
        return datetime.fromisoformat(row[0])
    except ValueError:
        return None
