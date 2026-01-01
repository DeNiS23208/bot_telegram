import os
import aiosqlite
import logging
from datetime import datetime, timedelta
from typing import Optional
from dotenv import load_dotenv
import logging

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "bot.db")
logger = logging.getLogger(__name__)

# Кэш для частых запросов (TTL 60 секунд)
_cache = {}
_cache_ttl = 60

def _get_cached(key: str):
    """Получает значение из кэша если оно еще актуально"""
    if key in _cache:
        value, timestamp = _cache[key]
        if (datetime.utcnow() - timestamp).total_seconds() < _cache_ttl:
            return value
        del _cache[key]
    return None

def _set_cached(key: str, value):
    """Сохраняет значение в кэш"""
    _cache[key] = (value, datetime.utcnow())

def _clear_cache():
    """Очищает весь кэш"""
    _cache.clear()

async def init_db() -> None:
    """Инициализирует базу данных и создает индексы для оптимизации"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Включаем WAL режим для лучшей производительности
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA cache_size=10000")
        await db.execute("PRAGMA temp_store=MEMORY")
        
        # Создаем таблицы
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
                starts_at TEXT,
                auto_renewal_enabled INTEGER DEFAULT 0,
                saved_payment_method_id TEXT,
                subscription_expired_notified INTEGER DEFAULT 0,
                FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
                             )
                         """)
        
        # Добавляем колонку subscription_expired_notified, если её нет
        try:
            await db.execute("ALTER TABLE subscriptions ADD COLUMN subscription_expired_notified INTEGER DEFAULT 0")
            await db.commit()
        except Exception:
            pass
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                payment_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
                         )
                         """)

        # Создаем индексы для оптимизации запросов
        await db.execute("CREATE INDEX IF NOT EXISTS idx_payments_telegram_id ON payments(telegram_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_payments_created_at ON payments(created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_payments_telegram_status ON payments(telegram_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_expires_at ON subscriptions(expires_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_auto_renewal ON subscriptions(auto_renewal_enabled)")
        
        await db.commit()
        logger.info("✅ База данных инициализирована с оптимизациями")


async def ensure_user(telegram_id: int, username: Optional[str]) -> None:
    """Создает пользователя если его нет (оптимизированная версия)"""
    cache_key = f"user_exists_{telegram_id}"
    if _get_cached(cache_key):
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, created_at) VALUES (?, ?, ?)",
            (telegram_id, username, datetime.utcnow().isoformat())
        )
        await db.commit()
    _set_cached(cache_key, True)


async def get_subscription_expires_at(telegram_id: int) -> Optional[datetime]:
    """Получает дату окончания подписки (с кэшированием)"""
    cache_key = f"sub_expires_{telegram_id}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached
    
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT expires_at FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cur.fetchone()

    if not row or not row[0]:
        return None

    try:
        result = datetime.fromisoformat(row[0])
        _set_cached(cache_key, result)
        return result
    except ValueError:
        return None


async def get_subscription_starts_at(telegram_id: int) -> Optional[datetime]:
    """Получает дату начала подписки (с кэшированием)"""
    cache_key = f"sub_starts_{telegram_id}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached
    
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT starts_at FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cur.fetchone()

    if not row or not row[0]:
        return None

    try:
        result = datetime.fromisoformat(row[0])
        _set_cached(cache_key, result)
        return result
    except ValueError:
        return None


async def get_subscription_info(telegram_id: int) -> Optional[dict]:
    """Получает всю информацию о подписке одним запросом (оптимизация)"""
    cache_key = f"sub_info_{telegram_id}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached
    
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT expires_at, starts_at, auto_renewal_enabled, saved_payment_method_id, subscription_expired_notified
            FROM subscriptions WHERE telegram_id = ?
            """,
            (telegram_id,)
        )
        row = await cur.fetchone()
    
    if not row:
        return None
    
    result = {
        'expires_at': datetime.fromisoformat(row[0]) if row[0] else None,
        'starts_at': datetime.fromisoformat(row[1]) if row[1] else None,
        'auto_renewal_enabled': bool(row[2]),
        'saved_payment_method_id': row[3],
        'subscription_expired_notified': bool(row[4])
    }
    _set_cached(cache_key, result)
    return result


async def activate_subscription_days(telegram_id: int, days: int = 30) -> tuple[datetime, datetime]:
    """Активирует подписку на N дней (оптимизированная версия)"""
    starts_at = datetime.utcnow()
    expires_at = starts_at + timedelta(days=days)
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Гарантируем, что юзер существует
        await db.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, created_at) VALUES (?, ?, ?)",
            (telegram_id, None, datetime.utcnow().isoformat())
        )
        
        # Upsert подписки
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
        
        # Очищаем кэш для этого пользователя
        _clear_cache()
    
    return starts_at, expires_at


async def get_saved_payment_method_id(telegram_id: int) -> Optional[str]:
    """Получает сохраненный payment_method_id (с кэшированием)"""
    cache_key = f"payment_method_{telegram_id}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached
    
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT saved_payment_method_id FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cur.fetchone()
    result = row[0] if row and row[0] else None
    _set_cached(cache_key, result)
    return result


async def is_auto_renewal_enabled(telegram_id: int) -> bool:
    """Проверяет, включено ли автопродление (с кэшированием)"""
    cache_key = f"auto_renewal_{telegram_id}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached
    
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT auto_renewal_enabled FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cur.fetchone()
    result = bool(row and row[0]) if row else False
    _set_cached(cache_key, result)
    return result


async def set_auto_renewal(telegram_id: int, enabled: bool, payment_method_id: Optional[str] = None) -> bool:
    """Включает/выключает автопродление (оптимизированная версия)"""
    async with aiosqlite.connect(DB_PATH) as db:
        if enabled:
            if payment_method_id:
                await db.execute(
                    "UPDATE subscriptions SET auto_renewal_enabled = ?, saved_payment_method_id = ? WHERE telegram_id = ?",
                    (1, payment_method_id, telegram_id)
                )
            else:
                cur = await db.execute(
                    "SELECT saved_payment_method_id FROM subscriptions WHERE telegram_id = ?",
                    (telegram_id,)
                )
                row = await cur.fetchone()
                if not row or not row[0]:
                    return False
                await db.execute(
                    "UPDATE subscriptions SET auto_renewal_enabled = ? WHERE telegram_id = ?",
                    (1, telegram_id)
                )
        else:
            await db.execute(
                "UPDATE subscriptions SET auto_renewal_enabled = ?, saved_payment_method_id = NULL WHERE telegram_id = ?",
                (0, telegram_id)
            )
        await db.commit()
        _clear_cache()  # Очищаем кэш после изменения
    return True


async def save_payment_method(telegram_id: int, payment_method_id: str) -> None:
    """Сохраняет payment_method_id (оптимизированная версия)"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE subscriptions SET saved_payment_method_id = ? WHERE telegram_id = ?",
            (payment_method_id, telegram_id)
        )
        await db.commit()
        _clear_cache()


async def delete_payment_method(telegram_id: int) -> bool:
    """Удаляет сохраненный способ оплаты (оптимизированная версия)"""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT saved_payment_method_id FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cur.fetchone()
        
        if not row or not row[0]:
            return False
        
        await db.execute(
            "UPDATE subscriptions SET saved_payment_method_id = NULL, auto_renewal_enabled = 0 WHERE telegram_id = ?",
            (telegram_id,)
        )
        await db.commit()
        _clear_cache()
        return True


async def save_payment(telegram_id: int, payment_id: str, status: str = "pending") -> None:
    """Сохраняет платеж (оптимизированная версия)"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO payments (telegram_id, payment_id, status, created_at) VALUES (?, ?, ?, ?)",
            (telegram_id, payment_id, status, datetime.utcnow().isoformat())
        )
        await db.commit()


async def update_payment_status(payment_id: str, status: str) -> None:
    """Обновляет статус платежа (оптимизированная версия)"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE payments SET status = ? WHERE payment_id = ?",
            (status, payment_id)
        )
        await db.commit()


async def get_latest_payment_id(telegram_id: int) -> Optional[str]:
    """Получает последний payment_id (оптимизированная версия с индексом)"""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT payment_id FROM payments WHERE telegram_id = ? ORDER BY id DESC LIMIT 1",
            (telegram_id,)
        )
        row = await cur.fetchone()
    return row[0] if row else None


async def get_active_pending_payment(telegram_id: int, minutes: int = 10) -> Optional[tuple[str, str]]:
    """Получает активный pending платеж (оптимизированная версия)"""
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
    """Проверяет, есть ли пользователь в списке оплативших (с кэшированием)"""
    cache_key = f"user_allowed_{telegram_user_id}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT 1 FROM approved_users WHERE telegram_user_id = ?",
                (telegram_user_id,)
            )
            row = await cur.fetchone()
            result = row is not None
            _set_cached(cache_key, result)
            return result
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
        _clear_cache()


async def get_all_active_subscriptions() -> list[tuple[int, str]]:
    """Получает все активные подписки (telegram_id, expires_at)"""
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.utcnow().isoformat()
        cur = await db.execute(
            "SELECT telegram_id, expires_at FROM subscriptions WHERE expires_at > ?",
            (now,)
        )
        rows = await cur.fetchall()
    return [(row[0], row[1]) for row in rows]


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
    """Получает telegram_user_id по invite_link (оптимизированная версия)"""
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
    """Получает последнюю активную ссылку-приглашение (оптимизированная версия)"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='invite_links'"
            )
            table_exists = await cur.fetchone()
            
            if not table_exists:
                return None
            
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


# ================== ФУНКЦИИ ДЛЯ ОЧИСТКИ СТАРЫХ ДАННЫХ ==================

async def cleanup_old_payments(days: int = 90) -> int:
    """
    Удаляет старые платежи старше N дней (кроме успешных)
    Возвращает количество удаленных записей
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cutoff_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cur = await db.execute(
            """
            DELETE FROM payments 
            WHERE created_at < ? AND status NOT IN ('succeeded', 'pending')
            """,
            (cutoff_date,)
        )
        deleted = cur.rowcount
        await db.commit()
        logger.info(f"🧹 Удалено {deleted} старых платежей (старше {days} дней)")
        return deleted


async def cleanup_old_invite_links(days: int = 180) -> int:
    """
    Удаляет старые отозванные ссылки старше N дней
    Возвращает количество удаленных записей
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cutoff_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cur = await db.execute(
            """
            DELETE FROM invite_links 
            WHERE revoked = 1 AND created_at < ?
            """,
            (cutoff_date,)
        )
        deleted = cur.rowcount
        await db.commit()
        logger.info(f"🧹 Удалено {deleted} старых отозванных ссылок (старше {days} дней)")
        return deleted


async def cleanup_old_processed_payments(days: int = 90) -> int:
    """
    Удаляет старые записи processed_payments старше N дней
    Возвращает количество удаленных записей
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cutoff_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cur = await db.execute(
            "DELETE FROM processed_payments WHERE processed_at < ?",
            (cutoff_date,)
        )
        deleted = cur.rowcount
        await db.commit()
        logger.info(f"🧹 Удалено {deleted} старых записей processed_payments (старше {days} дней)")
        return deleted


async def cleanup_old_data():
    """Очищает все старые данные (вызывается по расписанию)"""
    total_deleted = 0
    total_deleted += await cleanup_old_payments(days=90)
    total_deleted += await cleanup_old_invite_links(days=180)
    total_deleted += await cleanup_old_processed_payments(days=90)
    logger.info(f"✅ Очистка завершена, удалено {total_deleted} записей")
    return total_deleted

