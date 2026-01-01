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
                             expires_at
                             TEXT,
                             starts_at
                             TEXT,
                             auto_renewal_enabled
                             INTEGER
                             DEFAULT
                             0,
                             saved_payment_method_id
                             TEXT,
                             subscription_expired_notified
                             INTEGER
                             DEFAULT
                             0,
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
        # Добавляем колонку subscription_expired_notified, если её нет (для существующих БД)
        try:
            await db.execute("ALTER TABLE subscriptions ADD COLUMN subscription_expired_notified INTEGER DEFAULT 0")
            await db.commit()
        except Exception:
            pass  # Колонка уже существует
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


async def activate_subscription_days(telegram_id: int, days: int = 30) -> tuple[datetime, datetime]:
    """
    Активирует подписку на N дней от текущего момента (UTC).
    Если запись уже есть — обновляет expires_at и starts_at.
    Возвращает (starts_at, expires_at)
    """
    starts_at = datetime.utcnow()
    expires_at = starts_at + timedelta(days=days)

    async with aiosqlite.connect(DB_PATH) as db:
        # гарантируем, что юзер существует
        await db.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, created_at) VALUES (?, ?, ?)",
            (telegram_id, None, datetime.utcnow().isoformat())
        )

        # upsert подписки (сохраняем дату начала и окончания)
        # ВАЖНО: При обновлении сохраняем auto_renewal_enabled и saved_payment_method_id
        # При активации новой подписки сбрасываем флаг subscription_expired_notified
        await db.execute(
            """
            INSERT INTO subscriptions (telegram_id, expires_at, starts_at, subscription_expired_notified)
            VALUES (?, ?, ?, 0) ON CONFLICT(telegram_id) DO
            UPDATE SET expires_at=excluded.expires_at, starts_at=excluded.starts_at,
                       auto_renewal_enabled=COALESCE(subscriptions.auto_renewal_enabled, 0),
                       saved_payment_method_id=COALESCE(subscriptions.saved_payment_method_id, NULL),
                       subscription_expired_notified=0
            """,
            (telegram_id, expires_at.isoformat(), starts_at.isoformat())
        )
        await db.commit()

    return starts_at, expires_at


async def get_subscription_starts_at(telegram_id: int) -> Optional[datetime]:
    """Получает дату начала подписки"""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT starts_at FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cur.fetchone()

    if not row or not row[0]:
        return None

    try:
        return datetime.fromisoformat(row[0])
    except ValueError:
        return None


async def get_saved_payment_method_id(telegram_id: int) -> Optional[str]:
    """Получает сохраненный payment_method_id пользователя"""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT saved_payment_method_id FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cur.fetchone()
    return row[0] if row and row[0] else None


async def is_auto_renewal_enabled(telegram_id: int) -> bool:
    """Проверяет, включено ли автопродление"""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT auto_renewal_enabled FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cur.fetchone()
    return bool(row and row[0]) if row else False


async def set_auto_renewal(telegram_id: int, enabled: bool, payment_method_id: Optional[str] = None) -> bool:
    """
    Включает/выключает автопродление
    Возвращает True если успешно, False если нет сохраненного метода оплаты
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Проверяем, есть ли сохраненный метод оплаты
        if enabled:
            if payment_method_id:
                # Обновляем payment_method_id и включаем автопродление
                await db.execute(
                    "UPDATE subscriptions SET auto_renewal_enabled = ?, saved_payment_method_id = ? WHERE telegram_id = ?",
                    (1, payment_method_id, telegram_id)
                )
            else:
                # Проверяем, есть ли уже сохраненный метод
                cur = await db.execute(
                    "SELECT saved_payment_method_id FROM subscriptions WHERE telegram_id = ?",
                    (telegram_id,)
                )
                row = await cur.fetchone()
                if not row or not row[0]:
                    return False  # Нет сохраненного метода оплаты
                # Включаем автопродление без изменения payment_method_id
                await db.execute(
                    "UPDATE subscriptions SET auto_renewal_enabled = ? WHERE telegram_id = ?",
                    (1, telegram_id)
                )
        else:
            # Выключаем автопродление И удаляем сохраненный способ оплаты
            await db.execute(
                "UPDATE subscriptions SET auto_renewal_enabled = ?, saved_payment_method_id = NULL WHERE telegram_id = ?",
                (0, telegram_id)
            )
        await db.commit()
    return True


async def save_payment_method(telegram_id: int, payment_method_id: str) -> None:
    """Сохраняет payment_method_id для пользователя"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE subscriptions SET saved_payment_method_id = ? WHERE telegram_id = ?",
            (payment_method_id, telegram_id)
        )
        await db.commit()


async def delete_payment_method(telegram_id: int) -> bool:
    """
    Удаляет сохраненный способ оплаты и отключает автопродление
    Возвращает True если способ оплаты был удален, False если его не было
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Проверяем, есть ли сохраненный способ оплаты
        cur = await db.execute(
            "SELECT saved_payment_method_id FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cur.fetchone()
        
        if not row or not row[0]:
            return False  # Нет сохраненного способа оплаты
        
        # Удаляем способ оплаты и отключаем автопродление
        await db.execute(
            "UPDATE subscriptions SET saved_payment_method_id = NULL, auto_renewal_enabled = 0 WHERE telegram_id = ?",
            (telegram_id,)
        )
        await db.commit()
        return True


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


async def is_user_allowed(telegram_user_id: int) -> bool:
    """
    Проверяет, есть ли пользователь в списке оплативших (асинхронная версия)
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT 1 FROM approved_users WHERE telegram_user_id = ?",
                (telegram_user_id,)
            )
            row = await cur.fetchone()
            return row is not None
    except Exception:
        return False


async def set_subscription_expired_notified(telegram_id: int, notified: bool = True) -> None:
    """Помечает, что уведомление об истечении подписки было отправлено"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE subscriptions SET subscription_expired_notified = ? WHERE telegram_id = ?",
            (1 if notified else 0, telegram_id)
        )
        await db.commit()


async def get_subscription_expired_notified(telegram_id: int) -> bool:
    """Проверяет, было ли отправлено уведомление об истечении подписки"""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT subscription_expired_notified FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cur.fetchone()
    return bool(row and row[0]) if row else False


async def get_telegram_user_id_by_invite_link(invite_link: str) -> Optional[int]:
    """
    Получает telegram_user_id по invite_link
    Возвращает telegram_user_id или None если ссылка не найдена
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT telegram_user_id FROM invite_links WHERE invite_link = ? AND revoked = 0",
                (invite_link,)
            )
            row = await cur.fetchone()
            return int(row[0]) if row and row[0] else None
    except Exception as e:
        logger.error(f"❌ Ошибка получения telegram_user_id по invite_link: {e}")
        return None


async def get_invite_link(telegram_id: int) -> Optional[str]:
    """
    Получает последнюю активную ссылку-приглашение для пользователя
    Возвращает ссылку или None если не найдена
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Сначала проверяем, есть ли таблица invite_links
            cur = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='invite_links'"
            )
            table_exists = await cur.fetchone()
            
            if not table_exists:
                return None
            
            # Получаем последнюю неотозванную ссылку для пользователя
            cur = await db.execute(
                """
                SELECT invite_link 
                FROM invite_links 
                WHERE telegram_user_id = ? AND (revoked IS NULL OR revoked = 0)
                ORDER BY created_at DESC 
                LIMIT 1
                """,
                (telegram_id,)
            )
            row = await cur.fetchone()
            return row[0] if row and row[0] else None
    except Exception:
        return None
