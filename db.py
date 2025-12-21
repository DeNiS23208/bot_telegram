import os
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "bot.db")


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
                         CREATE TABLE IF NOT EXISTS users
                         (
                             telegram_id
                             INTEGER
                             PRIMARY
                             KEY,
                             username
                             TEXT,
                             created_at
                             TEXT
                             NOT
                             NULL
                         )
                         """)
        await db.execute("""
                         CREATE TABLE IF NOT EXISTS subscriptions
                         (
                             telegram_id
                             INTEGER
                             PRIMARY
                             KEY,
                             activated_at
                             TEXT,
                             expires_at
                             TEXT,
                             FOREIGN
                             KEY
                         (
                             telegram_id
                         ) REFERENCES users
                         (
                             telegram_id
                         )
                             )
                         """)
        
        # Миграция: добавляем поле activated_at, если его нет
        try:
            await db.execute("ALTER TABLE subscriptions ADD COLUMN activated_at TEXT")
            await db.commit()
        except Exception:
            # Поле уже существует, игнорируем ошибку
            pass
        await db.execute("""
                         CREATE TABLE IF NOT EXISTS payments
                         (
                             id
                             INTEGER
                             PRIMARY
                             KEY
                             AUTOINCREMENT,
                             telegram_id
                             INTEGER
                             NOT
                             NULL,
                             payment_id
                             TEXT
                             NOT
                             NULL
                             UNIQUE,
                             status
                             TEXT
                             NOT
                             NULL,
                             created_at
                             TEXT
                             NOT
                             NULL
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


async def get_subscription_activated_at(telegram_id: int) -> Optional[datetime]:
    """Получает дату активации подписки"""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT activated_at FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cur.fetchone()

    if not row or not row[0]:
        return None

    try:
        return datetime.fromisoformat(row[0])
    except ValueError:
        return None


async def activate_subscription_days(telegram_id: int, days: int = 30) -> datetime:
    """
    Активирует подписку на N дней от текущего момента (UTC).
    Если запись уже есть — обновляет expires_at.
    """
    expires_at = datetime.utcnow() + timedelta(days=days)

    async with aiosqlite.connect(DB_PATH) as db:
        # гарантируем, что юзер существует
        await db.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, created_at) VALUES (?, ?, ?)",
            (telegram_id, None, datetime.utcnow().isoformat())
        )

        # upsert подписки
        await db.execute(
            """
            INSERT INTO subscriptions (telegram_id, expires_at)
            VALUES (?, ?) ON CONFLICT(telegram_id) DO
            UPDATE SET expires_at=excluded.expires_at
            """,
            (telegram_id, expires_at.isoformat())
        )
        await db.commit()

    return expires_at


async def save_payment(telegram_id: int, payment_id: str, status: str = "pending") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO payments (telegram_id, payment_id, status, created_at) VALUES (?, ?, ?, ?)",
            (telegram_id, payment_id, status, datetime.utcnow().isoformat())
        )
        await db.commit()


async def update_payment_status(payment_id: str, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE payments SET status = ? WHERE payment_id = ?",
            (status, payment_id)
        )
        await db.commit()


async def get_latest_payment_id(telegram_id: int) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT payment_id FROM payments WHERE telegram_id = ? ORDER BY id DESC LIMIT 1",
            (telegram_id,)
        )
        row = await cur.fetchone()
    return row[0] if row else None


async def get_active_pending_payment(telegram_id: int, minutes: int = 10) -> Optional[tuple]:
    """
    Получает активный pending платеж, созданный менее N минут назад
    Возвращает (payment_id, created_at) или None
    """
    cutoff_time = datetime.utcnow() - timedelta(minutes=minutes)
    
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT payment_id, created_at 
            FROM payments 
            WHERE telegram_id = ? 
            AND status = 'pending' 
            AND created_at > ?
            ORDER BY id DESC 
            LIMIT 1
            """,
            (telegram_id, cutoff_time.isoformat())
        )
        row = await cur.fetchone()
    return row if row else None


async def get_active_pending_payment(telegram_id: int, minutes: int = 10) -> Optional[tuple[str, str]]:
    """
    Получает активный pending платеж пользователя, созданный менее N минут назад
    Возвращает (payment_id, created_at) или None
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cutoff_time = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
        cur = await db.execute(
            """
            SELECT payment_id, created_at 
            FROM payments 
            WHERE telegram_id = ? AND status = 'pending' AND created_at > ?
            ORDER BY id DESC LIMIT 1
            """,
            (telegram_id, cutoff_time)
        )
        row = await cur.fetchone()
    return (row[0], row[1]) if row else None
