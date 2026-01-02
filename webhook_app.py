import os
import aiosqlite
import asyncio
from datetime import datetime, timedelta, timezone
import logging

from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv
from aiogram import Bot
from aiogram.types import ChatJoinRequest, ReplyKeyboardMarkup, KeyboardButton
from yookassa import Payment, Configuration
from yookassa.domain.notification import WebhookNotificationFactory

from utils import format_datetime_moscow
from config import (
    PAYMENT_LINK_VALID_MINUTES,
    SUBSCRIPTION_DAYS,
    SUBSCRIPTION_EXPIRING_NOTIFICATION_DAYS,
    SUBSCRIPTION_EXPIRING_NOTIFICATION_WINDOW_HOURS,
    CHECK_EXPIRED_PAYMENTS_INTERVAL_SECONDS,
    CHECK_EXPIRED_SUBSCRIPTIONS_INTERVAL_SECONDS,
    CHECK_EXPIRING_SUBSCRIPTIONS_INTERVAL_SECONDS,
    MAX_NOTIFIED_USERS_CACHE_SIZE,
    PAYMENT_AMOUNT_RUB,
    is_bonus_week_active,
    get_bonus_week_end,
    get_current_subscription_price,
    get_current_subscription_duration,
    get_production_subscription_price,
    get_production_subscription_duration,
    dni_prazdnika,
    vremya_sms,
    BONUS_WEEK_PRICE_RUB,
)
from db import is_user_allowed, cleanup_old_data
from telegram_utils import safe_send_message, safe_create_invite_link

def format_subscription_duration(days: float) -> str:
    """Форматирует длительность подписки: показывает минуты если < 1 дня, иначе дни"""
    if days < 1:
        minutes = int(days * 1440)
        if minutes == 1:
            return "1 минута"
        elif 2 <= minutes <= 4:
            return f"{minutes} минуты"
        else:
            return f"{minutes} минут"
    else:
        days_int = int(days)
        if days_int == 1:
            return "1 день"
        elif 2 <= days_int <= 4:
            return f"{days_int} дня"
        else:
            return f"{days_int} дней"

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "/opt/bot_telegram/bot.db")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in .env")

if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
    raise RuntimeError("YOOKASSA credentials missing in .env")

Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

# ================== APP ==================
app = FastAPI()
bot = Bot(token=BOT_TOKEN)

# Запускаем фоновые задачи для проверки истекших платежей и подписок
async def cleanup_old_data_task():
    """Фоновая задача для очистки старых данных (запускается раз в день)"""
    # Ждем 1 час после старта, затем запускаем каждые 24 часа
    await asyncio.sleep(3600)
    
    while True:
        try:
            logger.info("🧹 Запуск очистки старых данных...")
            deleted = await cleanup_old_data()
            logger.info(f"✅ Очистка завершена, удалено {deleted} записей")
            # Запускаем очистку раз в день (24 часа)
            await asyncio.sleep(86400)
        except Exception as e:
            logger.error(f"❌ Ошибка при очистке старых данных: {e}")
            # При ошибке ждем 6 часов перед следующей попыткой
            await asyncio.sleep(21600)


@app.on_event("startup")
async def startup_event():
    """Запускаем фоновые задачи при старте приложения"""
    # Инициализируем таблицы
    await init_webhook_tables()
    # Запускаем фоновые задачи
    asyncio.create_task(check_expired_payments())
    asyncio.create_task(check_expired_subscriptions())
    asyncio.create_task(check_subscriptions_expiring_soon())
    asyncio.create_task(check_bonus_week_ending_soon())  # Уведомления о окончании бонусной недели
    asyncio.create_task(check_bonus_week_transition_to_production())  # Уведомления о переходе в продакшн режим
    asyncio.create_task(cleanup_old_data_task())  # Добавляем задачу очистки
    logger.info("✅ Фоновые задачи проверки истекших платежей и подписок запущены")


# Обработчик возврата с ЮKassa (если пользователь вернулся без оплаты)
@app.get("/payment/return")
async def payment_return(request: Request):
    """
    Обработчик возврата пользователя с формы оплаты ЮKassa
    Если пользователь вернулся без оплаты, проверяем статус и отправляем уведомление
    """
    # Получаем telegram_user_id из query параметров (передается в return_url)
    tg_user_id_param = request.query_params.get("user_id")
    # Также пытаемся получить payment_id напрямую (если ЮKassa передает)
    payment_id = request.query_params.get("payment_id") or request.query_params.get("orderId")
    
    logger.info(f"📥 Получен возврат с формы оплаты: user_id={tg_user_id_param}, payment_id={payment_id}, query_params={dict(request.query_params)}")
    
    tg_user_id = None
    
    # Если есть payment_id, получаем tg_user_id из метаданных платежа
    if payment_id:
        try:
            payment = Payment.find_one(payment_id)
            meta = payment.metadata or {}
            tg_user_id = meta.get("telegram_user_id")
            logger.info(f"📋 Получен tg_user_id из метаданных платежа: {tg_user_id}")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка получения платежа {payment_id}: {e}")
    
    # Если tg_user_id не получен из платежа, используем из параметров
    if not tg_user_id and tg_user_id_param:
        try:
            tg_user_id = int(tg_user_id_param)
            logger.info(f"📋 Использован tg_user_id из параметров: {tg_user_id}")
        except ValueError:
            logger.warning(f"⚠️ Неверный формат user_id: {tg_user_id_param}")
    
    if tg_user_id:
        tg_user_id = int(tg_user_id)
        
        # Если нет payment_id, находим последний pending платеж пользователя
        if not payment_id:
            try:
                from db import get_active_pending_payment
                active_payment = await get_active_pending_payment(tg_user_id, minutes=PAYMENT_LINK_VALID_MINUTES * 3)
                if active_payment:
                    payment_id, created_at = active_payment
                    logger.info(f"📋 Найден последний pending платеж: {payment_id}")
            except Exception as e:
                logger.warning(f"⚠️ Ошибка поиска последнего платежа: {e}")
                payment_id = None
        
        # Если есть payment_id, проверяем статус платежа
        if payment_id:
            try:
                payment = Payment.find_one(payment_id)
                current_status = payment.status
                logger.info(f"📋 Статус платежа {payment_id}: {current_status}")
                
                # Если платеж все еще pending, значит пользователь не оплатил (вышел из формы)
                if current_status == "pending":
                    # Проверяем, есть ли активная подписка
                    has_active = await has_active_subscription(tg_user_id)
                    
                    if not has_active:
                        # Не отправляем уведомление, если пользователь сам вышел из формы
                        # Просто помечаем платеж как обработанный, чтобы фоновая задача не отправляла уведомление об истечении
                        # Но НЕ меняем статус на canceled, чтобы можно было оплатить позже
                        logger.info(f"ℹ️ Пользователь {tg_user_id} вернулся с формы оплаты (платеж pending) - уведомление не отправлено")
                    else:
                        logger.info(f"ℹ️ Пользователь {tg_user_id} вернулся с формы оплаты, но у него уже есть активная подписка")
                
                # Если платеж отменен, webhook должен был обработать это
                elif current_status == "canceled":
                    has_active = await has_active_subscription(tg_user_id)
                    if not has_active:
                        # Уже должен был быть обработан через webhook, но на всякий случай проверим
                        logger.info(f"ℹ️ Платеж {payment_id} уже отменен, webhook должен был обработать")
                        
            except Exception as e:
                logger.error(f"❌ Ошибка проверки платежа {payment_id}: {e}")
                import traceback
                traceback.print_exc()
        else:
            # Если не нашли платеж, не отправляем уведомление (пользователь сам вышел)
            logger.info(f"ℹ️ Пользователь {tg_user_id} вернулся с формы оплаты (платеж не найден) - уведомление не отправлено")
    else:
        logger.warning(f"⚠️ Не удалось определить telegram_user_id для обработки возврата")
    
    # Возвращаем простую страницу или редирект
    return {"status": "ok", "message": "Вы вернулись с формы оплаты"}

# ================== DB (ОПТИМИЗИРОВАННЫЕ ASYNC ФУНКЦИИ) ==================
async def init_webhook_tables():
    """Инициализирует таблицы для webhook (вызывается один раз при старте)"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS processed_payments (
            payment_id TEXT PRIMARY KEY,
            processed_at TEXT NOT NULL
        )
    """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS approved_users (
            telegram_user_id INTEGER PRIMARY KEY,
            approved_at TEXT NOT NULL
        )
    """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS invite_links (
            invite_link TEXT PRIMARY KEY,
            telegram_user_id INTEGER NOT NULL,
            payment_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revoked INTEGER DEFAULT 0,
            FOREIGN KEY (telegram_user_id) REFERENCES approved_users(telegram_user_id)
        )
    """)
        # Создаем индексы для оптимизации
        await db.execute("CREATE INDEX IF NOT EXISTS idx_invite_links_user_id ON invite_links(telegram_user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_invite_links_revoked ON invite_links(revoked)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_processed_payments_at ON processed_payments(processed_at)")
        await db.commit()


async def already_processed(payment_id: str) -> bool:
    """Проверяет, был ли платеж уже обработан (async версия)"""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM processed_payments WHERE payment_id = ?", (payment_id,))
        row = await cur.fetchone()
    return row is not None


async def mark_processed(payment_id: str):
    """Помечает платеж как обработанный (async версия)"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
        "INSERT OR IGNORE INTO processed_payments(payment_id, processed_at) VALUES (?, ?)",
        (payment_id, datetime.now(timezone.utc).isoformat())
    )
        await db.commit()


async def allow_user(tg_user_id: int):
    """Добавляет пользователя в список одобренных (async версия)"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
        "INSERT OR REPLACE INTO approved_users(telegram_user_id, approved_at) VALUES (?, ?)",
        (tg_user_id, datetime.now(timezone.utc).isoformat())
    )
        await db.commit()


async def save_invite_link(invite_link: str, telegram_user_id: int, payment_id: str):
    """Сохраняет информацию о созданной ссылке-приглашении (async версия)"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
        "INSERT OR REPLACE INTO invite_links(invite_link, telegram_user_id, payment_id, created_at) VALUES (?, ?, ?, ?)",
        (invite_link, telegram_user_id, payment_id, datetime.now(timezone.utc).isoformat())
    )
        await db.commit()


async def revoke_invite_link(invite_link: str):
    """Помечает ссылку как отозванную (async версия)"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
        "UPDATE invite_links SET revoked = 1 WHERE invite_link = ?",
        (invite_link,)
    )
        await db.commit()


async def get_main_menu_for_user(telegram_id: int) -> ReplyKeyboardMarkup:
    """Создает главное меню для пользователя с учетом статуса подписки"""
    # ВАЖНО: Очищаем кэш перед проверкой, чтобы получить актуальные данные
    from db import _clear_cache
    _clear_cache()
    
    # Сначала проверяем наличие активной подписки
    from db import get_subscription_expires_at, is_auto_renewal_enabled
    from datetime import timezone
    expires_at = await get_subscription_expires_at(telegram_id)
    now = datetime.now(timezone.utc)
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    has_active_subscription = expires_at and expires_at > now
    auto_renewal_enabled = await is_auto_renewal_enabled(telegram_id)
    show_manage_button = has_active_subscription and auto_renewal_enabled
    
    # КРИТИЧЕСКИ ВАЖНО: Показываем бонусное меню ТОЛЬКО если:
    # 1. Бонусная неделя активна (is_bonus_week_active() = True)
    # 2. У пользователя НЕТ активной подписки с автопродлением (show_manage_button = False)
    # Если бонусная неделя закончилась - ВСЕГДА показываем продакшн меню, независимо от статуса автопродления
    # КРИТИЧЕСКИ ВАЖНО: Проверяем окончание бонусной недели ПО ВРЕМЕНИ - это приоритетная проверка
    from config import get_bonus_week_end
    bonus_week_end = get_bonus_week_end()
    if bonus_week_end.tzinfo is None:
        bonus_week_end = bonus_week_end.replace(tzinfo=timezone.utc)
    # ПРИОРИТЕТНАЯ ПРОВЕРКА: Если текущее время больше времени окончания бонусной недели - бонусная неделя ЗАКОНЧИЛАСЬ
    # Это проверка имеет приоритет над is_bonus_week_active()
    if now > bonus_week_end:
        bonus_week_active = False  # Принудительно устанавливаем, что бонусная неделя закончилась
        logger.info(f"🔍 Бонусная неделя закончилась по времени: now={now.isoformat()}, bonus_week_end={bonus_week_end.isoformat()}")
    else:
        # Только если бонусная неделя еще не закончилась по времени, проверяем is_bonus_week_active()
        bonus_week_active = is_bonus_week_active()
        if bonus_week_active:
            logger.info(f"🔍 Бонусная неделя активна: now={now.isoformat()}, bonus_week_end={bonus_week_end.isoformat()}")
    
    if bonus_week_active:
        if show_manage_button:
            # У пользователя есть активная подписка с автопродлением - показываем "Управление доступом"
            BTN_MANAGE_SUB = "⚙️ Управление доступом"
            BTN_ABOUT_1 = "ℹ️ О проекте"
            keyboard = [
                [KeyboardButton(text=BTN_MANAGE_SUB)],
                [KeyboardButton(text=BTN_ABOUT_1)],
            ]
            return ReplyKeyboardMarkup(
                keyboard=keyboard,
                resize_keyboard=True,
            )
        elif has_active_subscription and not auto_renewal_enabled:
            # КРИТИЧЕСКИ ВАЖНО: Если в бонусной неделе у пользователя есть активная подписка,
            # но автопродление отключено - показываем ТОЛЬКО "О проекте"
            BTN_ABOUT_1 = "ℹ️ О проекте"
            keyboard = [
                [KeyboardButton(text=BTN_ABOUT_1)],
            ]
            return ReplyKeyboardMarkup(
                keyboard=keyboard,
                resize_keyboard=True,
            )
        else:
            # У пользователя нет активной подписки - показываем бонусное меню
            BTN_BONUS_WEEK = "🎁 Бонус в честь запуска канала Наиля Хасанова"
            BTN_ABOUT_1 = "ℹ️ О проекте"
            keyboard = [
                [KeyboardButton(text=BTN_BONUS_WEEK)],
                [KeyboardButton(text=BTN_ABOUT_1)],
            ]
            return ReplyKeyboardMarkup(
                keyboard=keyboard,
                resize_keyboard=True,
            )
    
    # Бонусная неделя закончилась - ВСЕГДА показываем продакшн меню, независимо от статуса автопродления
    
    # Константы кнопок (должны совпадать с bot.py)
    BTN_PAY_1 = "💳 Получить доступ"
    BTN_MANAGE_SUB = "⚙️ Управление доступом"
    BTN_STATUS_1 = "📊 Статус доступа"
    BTN_ABOUT_1 = "ℹ️ О проекте"
    BTN_CHECK_1 = "🔍 Проверить оплату"
    BTN_SUPPORT = "💬 Поддержка"
    
    # Проверяем наличие активной подписки
    from db import get_subscription_expires_at, is_auto_renewal_enabled
    expires_at = await get_subscription_expires_at(telegram_id)
    now = datetime.now(timezone.utc)
    # Убеждаемся, что expires_at имеет timezone для сравнения
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    has_active_subscription = expires_at and expires_at > now
    
    # Проверяем, включено ли автопродление
    # Если автопродление отключено, показываем "Получить доступ" даже при активной подписке
    auto_renewal_enabled = await is_auto_renewal_enabled(telegram_id)
    # Показываем "Управление доступом" только если подписка активна И автопродление включено
    show_manage_button = has_active_subscription and auto_renewal_enabled
    
    # Если есть активная подписка с автопродлением - показываем "Управление доступом", иначе "Получить доступ"
    payment_button = BTN_MANAGE_SUB if show_manage_button else BTN_PAY_1
    
    keyboard = [
        [KeyboardButton(text=payment_button)],
        [KeyboardButton(text=BTN_STATUS_1)],
        [KeyboardButton(text=BTN_ABOUT_1)],
        [KeyboardButton(text=BTN_CHECK_1)],
        [KeyboardButton(text=BTN_SUPPORT)],
    ]
    
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
    )


async def activate_subscription(telegram_id: int, days: int = 30) -> tuple[datetime, datetime]:
    """Активирует подписку на N дней (асинхронная версия для webhook)
    Возвращает (starts_at, expires_at)"""
    from datetime import timezone
    starts_at = datetime.now(timezone.utc)
    expires_at = starts_at + timedelta(days=days)
    
    async with aiosqlite.connect(DB_PATH) as db_conn:
        # гарантируем, что юзер существует
        await db_conn.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, created_at) VALUES (?, ?, ?)",
            (telegram_id, None, datetime.now(timezone.utc).isoformat())
        )
        
        # upsert подписки (сохраняем дату начала и окончания)
        # При активации новой подписки сбрасываем флаг subscription_expired_notified
        await db_conn.execute(
            """
            INSERT INTO subscriptions (telegram_id, expires_at, starts_at, subscription_expired_notified)
            VALUES (?, ?, ?, 0) ON CONFLICT(telegram_id) DO
            UPDATE SET expires_at=excluded.expires_at, starts_at=excluded.starts_at,
                       subscription_expired_notified=0
            """,
            (telegram_id, expires_at.isoformat(), starts_at.isoformat())
        )
        await db_conn.commit()
        logger.info(f"💾 Подписка сохранена в БД: telegram_id={telegram_id}, expires_at={expires_at.isoformat()}, starts_at={starts_at.isoformat()}")
    
    return starts_at, expires_at


async def update_payment_status_async(payment_id: str, status: str):
    """Обновляет статус платежа (асинхронная версия)"""
    async with aiosqlite.connect(DB_PATH) as db_conn:
        await db_conn.execute(
            "UPDATE payments SET status = ? WHERE payment_id = ?",
            (status, payment_id)
        )
        await db_conn.commit()


async def has_active_subscription(telegram_id: int) -> bool:
    """Проверяет, есть ли у пользователя активная подписка"""
    from datetime import timezone
    async with aiosqlite.connect(DB_PATH) as db_conn:
        cursor = await db_conn.execute(
            "SELECT expires_at FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cursor.fetchone()
        
        if not row or not row[0]:
            return False
        
        try:
            from datetime import timezone
            expires_at = datetime.fromisoformat(row[0])
            # Если expires_at не имеет timezone, добавляем UTC
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return expires_at > now
        except ValueError:
            return False


async def get_expired_pending_payments():
    """Получает список платежей со статусом pending, которые старше N минут"""
    async with aiosqlite.connect(DB_PATH) as db_conn:
        # Платежи старше N минут со статусом pending (НЕ canceled и НЕ expired)
        cutoff_time = (datetime.now(timezone.utc) - timedelta(minutes=PAYMENT_LINK_VALID_MINUTES)).isoformat()
        cursor = await db_conn.execute(
            """
            SELECT telegram_id, payment_id, created_at 
            FROM payments 
            WHERE status = 'pending' 
            AND created_at < ?
            AND created_at > ?
            """,
            (cutoff_time, (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat())  # Только за последние 24 часа
        )
        rows = await cursor.fetchall()
        return rows


async def get_expired_subscriptions():
    """Получает список подписок, которые истекли"""
    async with aiosqlite.connect(DB_PATH) as db_conn:
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        # Подписки, которые уже истекли (проверяем с небольшим запасом для точности)
        cursor = await db_conn.execute(
            """
            SELECT telegram_id, expires_at, auto_renewal_enabled, saved_payment_method_id, starts_at
            FROM subscriptions 
            WHERE expires_at IS NOT NULL 
            AND expires_at <= ?
            """,
            (now_iso,)
        )
        rows = await cursor.fetchall()
        logger.debug(f"🔍 get_expired_subscriptions: найдено {len(rows)} истекших подписок (now={now_iso})")
        for row in rows:
            logger.debug(f"  - Пользователь {row[0]}: expires_at={row[1]}, auto_renewal={row[2]}, saved_method={bool(row[3]) if row[3] else False}")
        return rows


async def get_subscriptions_expiring_soon():
    """Получает список подписок, которые истекают через N дней (для уведомления)"""
    async with aiosqlite.connect(DB_PATH) as db_conn:
        now = datetime.now(timezone.utc)
        # Подписки, которые истекают через N дней (с небольшой погрешностью)
        target_date = now + timedelta(days=SUBSCRIPTION_EXPIRING_NOTIFICATION_DAYS)
        # Проверяем подписки, которые истекают в течение окна уведомления
        start_date = target_date.isoformat()
        end_date = (target_date + timedelta(hours=SUBSCRIPTION_EXPIRING_NOTIFICATION_WINDOW_HOURS)).isoformat()
        cursor = await db_conn.execute(
            """
            SELECT telegram_id, expires_at 
            FROM subscriptions 
            WHERE expires_at >= ? AND expires_at <= ?
            """,
            (start_date, end_date)
        )
        rows = await cursor.fetchall()
        return rows


async def check_expired_payments():
    """Проверяет истекшие платежи и уведомляет пользователей ровно через 10 минут после создания"""
    notified_payments = set()  # Отслеживаем, для каких платежей уже отправлено уведомление
    
    while True:
        try:
            await asyncio.sleep(CHECK_EXPIRED_PAYMENTS_INTERVAL_SECONDS)
            
            expired_payments = await get_expired_pending_payments()
            
            for telegram_id, payment_id, created_at in expired_payments:
                # Пропускаем, если уведомление уже было отправлено для этого платежа
                if payment_id in notified_payments:
                    continue
                
                # Проверяем точное время истечения - должно быть ровно 10 минут (с погрешностью ±1 минута)
                try:
                    created_at_dt = datetime.fromisoformat(created_at)
                    now = datetime.now(timezone.utc)
                    time_since_creation = (now - created_at_dt).total_seconds() / 60  # в минутах
                    
                    # Проверяем, что прошло ровно 10 минут (с погрешностью ±1 минута из-за интервала проверки)
                    # Это гарантирует, что уведомление будет отправлено в течение 1-2 минут после истечения 10 минут
                    if time_since_creation < PAYMENT_LINK_VALID_MINUTES - 1:
                        # Еще не истекло, пропускаем
                        continue
                    if time_since_creation > PAYMENT_LINK_VALID_MINUTES + 2:
                        # Уже прошло больше 12 минут, пропускаем (чтобы не отправлять повторно)
                        notified_payments.add(payment_id)
                        continue
                except Exception as time_error:
                    logger.warning(f"⚠️ Ошибка проверки времени для платежа {payment_id}: {time_error}")
                    # Продолжаем обработку, если не удалось проверить время
                
                # Проверяем актуальный статус платежа в ЮKassa
                try:
                    payment = Payment.find_one(payment_id)
                    current_status = payment.status
                    
                    # Если платеж все еще pending (не оплачен), уведомляем пользователя
                    if current_status == "pending":
                        # ПРОВЕРЯЕМ: есть ли у пользователя активная подписка
                        has_active = await has_active_subscription(telegram_id)
                        
                        if has_active:
                            # Если подписка активна - просто обновляем статус, не отправляем уведомление
                            await update_payment_status_async(payment_id, "expired")
                            notified_payments.add(payment_id)  # Помечаем как обработанный
                            logger.info(f"ℹ️ Платеж {payment_id} истек, но у пользователя {telegram_id} уже есть активная подписка - уведомление не отправлено")
                        else:
                            # Обновляем статус на expired
                            await update_payment_status_async(payment_id, "expired")
                            
                            # Уведомляем пользователя только если нет активной подписки (ОДИН РАЗ)
                            result = await safe_send_message(
                                bot=bot,
                                chat_id=telegram_id,
                                text=f"⏰ Срок действия ссылки на оплату истёк\n\n"
                                    "Вы открыли ссылку на оплату, но не завершили платёж.\n"
                                    f"Ссылка была действительна {PAYMENT_LINK_VALID_MINUTES} минут.\n\n"
                                    "Для оплаты доступа нажмите кнопку 💳 Получить доступ и перейдите по новой ссылке."
                                )
                            if result:
                                notified_payments.add(payment_id)  # Помечаем, что уведомление отправлено
                                logger.info(f"✅ Отправлено уведомление об истечении ссылки пользователю {telegram_id} для платежа {payment_id} (один раз, через {time_since_creation:.1f} минут после создания)")
                            else:
                                logger.warning(f"⚠️ Не удалось отправить уведомление об истечении ссылки пользователю {telegram_id}")
                    else:
                        # Если статус изменился (например, на canceled), обновляем в БД
                        await update_payment_status_async(payment_id, current_status)
                        notified_payments.add(payment_id)  # Помечаем как обработанный
                        
                except Exception as e:
                    logger.error(f"❌ Ошибка проверки платежа {payment_id}: {e}")
            
            # Очищаем старые записи из notified_payments (старше 24 часов), чтобы не накапливать память
            # Но это не критично, так как payment_id уникальны
            if len(notified_payments) > 1000:
                # Если накопилось слишком много, очищаем (в реальности это маловероятно)
                notified_payments.clear()
                logger.info("🧹 Очищен кэш notified_payments")
                    
        except Exception as e:
            logger.error(f"❌ Ошибка в фоновой задаче проверки платежей: {e}")
            await asyncio.sleep(10)  # Уменьшено для более точного срабатывания  # Ждем перед следующей попыткой


async def check_subscriptions_expiring_soon():
    """Проверяет подписки, которые истекают через N дней, и отправляет уведомления"""
    notified_users = set()  # Чтобы не отправлять несколько раз одному пользователю
    
    while True:
        try:
            await asyncio.sleep(CHECK_EXPIRING_SUBSCRIPTIONS_INTERVAL_SECONDS)
            
            # ВАЖНО: Не отправляем уведомления во время бонусной недели
            # Уведомления о конце бонусной недели отправляются отдельной функцией check_bonus_week_ending_soon
            if is_bonus_week_active():
                continue
            
            # Получаем подписки, которые истекают через N дней
            expiring_subs = await get_subscriptions_expiring_soon()
            
            for telegram_id, expires_at_str in expiring_subs:
                if telegram_id in notified_users:
                    continue
                    
                try:
                    expires_at = datetime.fromisoformat(expires_at_str)
                    now = datetime.now(timezone.utc)
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    days_left = (expires_at - now).days
                    
                    # Если осталось примерно N дней (с погрешностью ±1 день)
                    notification_days_min = SUBSCRIPTION_EXPIRING_NOTIFICATION_DAYS - 1
                    notification_days_max = SUBSCRIPTION_EXPIRING_NOTIFICATION_DAYS + 1
                    if notification_days_min <= days_left <= notification_days_max:
                        await safe_send_message(
                            bot=bot,
                            chat_id=telegram_id,
                            text=f"⏰ Внимание! Доступ истекает через {SUBSCRIPTION_EXPIRING_NOTIFICATION_DAYS} дня\n\n"
                                f"Ваш доступ действует до: {expires_at.date()}\n\n"
                                "Для продления доступа нажмите кнопку 💳 Получить доступ.\n"
                                "Если доступ не будет продлен, вас удалят из канала."
                        )
                        notified_users.add(telegram_id)
                        logger.info(f"✅ Отправлено уведомление о скором истечении подписки пользователю {telegram_id}")
                        
                except Exception as e:
                    logger.error(f"❌ Ошибка обработки уведомления для пользователя {telegram_id}: {e}")
            
            # Очищаем обработанных пользователей при достижении лимита
            if len(notified_users) > MAX_NOTIFIED_USERS_CACHE_SIZE:
                notified_users.clear()
                    
        except Exception as e:
            logger.error(f"❌ Ошибка в фоновой задаче проверки истекающих подписок: {e}")
            await asyncio.sleep(CHECK_EXPIRING_SUBSCRIPTIONS_INTERVAL_SECONDS)


async def attempt_auto_renewal(telegram_id: int, saved_payment_method_id: str, auto_amount: str, auto_duration: float, attempt_number: int) -> bool:
    """Выполняет одну попытку автопродления. Возвращает True если успешно, False если неудачно."""
    try:
        from payments import create_auto_payment, get_payment_status
        from db import activate_subscription_days, save_payment, update_payment_status, get_subscription_expires_at, increment_auto_renewal_attempts, reset_auto_renewal_attempts, set_auto_renewal
        
        CUSTOMER_EMAIL = os.getenv("PAYMENT_CUSTOMER_EMAIL", "test@example.com")
        
        logger.info(f"🔄 Попытка {attempt_number} автопродления для пользователя {telegram_id}: {auto_amount} руб, {auto_duration} дней")
        
        # Создаем автоматический платеж
        payment_id, payment_status = create_auto_payment(
            amount_rub=auto_amount,
            description=f"Автопродление доступа на канал ({format_subscription_duration(auto_duration)})",
            customer_email=CUSTOMER_EMAIL,
            telegram_user_id=telegram_id,
            payment_method_id=saved_payment_method_id,
        )
        
        # Сохраняем платеж
        await save_payment(telegram_id, payment_id, status=payment_status)
        
        # Ждем немного для обработки webhook
        await asyncio.sleep(3)
        
        # Проверяем статус платежа
        refreshed_status = get_payment_status(payment_id)
        await update_payment_status(payment_id, refreshed_status)
        
        if refreshed_status == "succeeded":
            # Платеж успешен - активируем подписку
            await activate_subscription_days(telegram_id, days=auto_duration)
            from db import _clear_cache
            _clear_cache()
            
            # Сбрасываем счетчик попыток при успехе
            await reset_auto_renewal_attempts(telegram_id)
            
            # Выдаем новую ссылку после успешного автопродления
            subscription_expires_at = await get_subscription_expires_at(telegram_id)
            link_expire_date = subscription_expires_at if subscription_expires_at else (datetime.now(timezone.utc) + timedelta(days=auto_duration))
            
            invite_link = await safe_create_invite_link(
                bot=bot,
                chat_id=CHANNEL_ID,
                creates_join_request=True,
                expire_date=link_expire_date
            )
            
            if not invite_link:
                invite_link = await safe_create_invite_link(
                    bot=bot,
                    chat_id=CHANNEL_ID,
                    creates_join_request=False,
                    member_limit=1,
                    expire_date=link_expire_date
                )
            
            if invite_link:
                await save_invite_link(invite_link, telegram_id, payment_id)
            
            # Отправляем уведомление об успешном автопродлении
            amount_float = float(auto_amount)
            if amount_float == 1:
                ruble_text = "рубль"
            elif 2 <= amount_float <= 4:
                ruble_text = "рубля"
            else:
                ruble_text = "рублей"
            
            # Получаем меню с кнопкой "Управление доступом"
            menu = await get_main_menu_for_user(telegram_id)
            
            message_text = (
                "✅ <b>Доступ автоматически продлен!</b>\n\n"
                f"Списано {auto_amount} {ruble_text} с вашего способа оплаты.\n"
                f"Доступ продлен на {format_subscription_duration(auto_duration)}.\n\n"
            )
            
            if invite_link:
                message_text += (
                    "Нажмите на ссылку ниже, чтобы попасть в канал:\n"
                    f"{invite_link}\n\n"
                    "⚠️ ВНИМАНИЕ: Ссылка одноразовая и персональная. Не передавайте её другим людям!"
                )
            else:
                message_text += "⚠️ Произошла ошибка при создании ссылки. Пожалуйста, свяжитесь с администратором."
            
            await safe_send_message(
                bot=bot,
                chat_id=telegram_id,
                text=message_text,
                parse_mode="HTML",
                reply_markup=menu
            )
            
            logger.info(f"✅ Автопродление успешно выполнено для пользователя {telegram_id}, попытка {attempt_number}, payment_id: {payment_id}")
            return True
        else:
            # Платеж не прошел - увеличиваем счетчик попыток
            await increment_auto_renewal_attempts(telegram_id)
            
            # Проверяем детали платежа для определения причины отказа
            insufficient_funds = False
            try:
                from yookassa import Payment as YooPayment
                payment_obj = YooPayment.find_one(payment_id)
                if hasattr(payment_obj, 'cancellation_details') and payment_obj.cancellation_details:
                    cd = payment_obj.cancellation_details
                    reason = None
                    if hasattr(cd, 'reason'):
                        reason = cd.reason
                    elif isinstance(cd, dict):
                        reason = cd.get('reason')
                    
                    if reason and ('insufficient_funds' in str(reason).lower() or 'not_enough_money' in str(reason).lower() or 'недостаточно' in str(reason).lower()):
                        insufficient_funds = True
                        logger.info(f"💰 Обнаружена недостаточность средств для пользователя {telegram_id}, payment_id: {payment_id}, reason: {reason}")
            except Exception as payment_check_error:
                logger.warning(f"⚠️ Ошибка проверки деталей платежа {payment_id}: {payment_check_error}")
            
            # Отправляем уведомление о неудачной попытке
            if insufficient_funds:
                await safe_send_message(
                    bot=bot,
                    chat_id=telegram_id,
                    text=(
                        "⚠️ <b>У вас недостаточно средств</b>\n\n"
                        "На вашей карте недостаточно средств для автопродления подписки.\n"
                        f"Попытка {attempt_number} из 3 не удалась.\n"
                        "Пожалуйста пополните баланс для успешного автопродления"
                    ),
                    parse_mode="HTML"
                )
            else:
                await safe_send_message(
                    bot=bot,
                    chat_id=telegram_id,
                    text=(
                        "⚠️ <b>Автопродление не удалось</b>\n\n"
                        "Не удалось списать средства с вашего способа оплаты.\n"
                        f"Попытка {attempt_number} из 3 не удалась."
                    ),
                    parse_mode="HTML"
                )
            
            logger.warning(f"⚠️ Автопродление не удалось для пользователя {telegram_id}, попытка {attempt_number}, статус: {refreshed_status}, insufficient_funds: {insufficient_funds}")
            return False
            
    except Exception as auto_error:
        logger.error(f"❌ Ошибка автопродления для пользователя {telegram_id}, попытка {attempt_number}: {auto_error}")
        import traceback
        traceback.print_exc()
        await increment_auto_renewal_attempts(telegram_id)
        return False


async def check_bonus_week_transition_to_production():
    """Проверяет переход в продакшн режим после окончания бонусной недели и выполняет автопродление:
    1. При окончании бонусной недели - первая попытка автопродления (сразу)
    2. Если неудачно - вторая попытка через 5 минут
    3. Если неудачно - третья попытка еще через 5 минут
    4. Если все 3 попытки неудачны - бан и меню с "Оплатить доступ"
    5. Если на любой попытке успешно - меню с "Управление доступом"
    """
    notified_users_production = set()  # Чтобы не отправлять несколько раз одному пользователю
    
    while True:
        try:
            await asyncio.sleep(10)  # Проверяем каждые 10 секунд для более точного срабатывания
            
            # Проверяем, закончилась ли бонусная неделя
            bonus_week_active = is_bonus_week_active()
            if bonus_week_active:
                notified_users_production.clear()
                continue
            
            from config import get_bonus_week_end, get_bonus_week_start
            bonus_week_end = get_bonus_week_end()
            bonus_week_start = get_bonus_week_start()
            if bonus_week_end.tzinfo is None:
                bonus_week_end = bonus_week_end.replace(tzinfo=timezone.utc)
            if bonus_week_start.tzinfo is None:
                bonus_week_start = bonus_week_start.replace(tzinfo=timezone.utc)
            
            now = datetime.now(timezone.utc)
            time_since_bonus_end = (now - bonus_week_end).total_seconds() / 60
            
            logger.info(f"🔄 check_bonus_week_transition_to_production: бонусная неделя закончилась {time_since_bonus_end:.1f} минут назад, проверяем подписки...")
            
            # КРИТИЧЕСКИ ВАЖНО: Получаем ВСЕ подписки (включая истекшие), которые были созданы во время бонусной недели
            # Это необходимо, потому что когда бонусная неделя заканчивается, подписка уже истекла
            # и не попадает в get_all_active_subscriptions()
            from db import get_subscription_info, get_last_auto_renewal_attempt_at, get_auto_renewal_attempts
            async with aiosqlite.connect(DB_PATH) as db_conn:
                cursor = await db_conn.execute(
                    """
                    SELECT telegram_id, expires_at, starts_at 
                    FROM subscriptions 
                    WHERE starts_at IS NOT NULL
                    """,
                )
                all_subs = await cursor.fetchall()
            
            logger.info(f"🔍 check_bonus_week_transition_to_production: найдено {len(all_subs)} подписок с starts_at")
            
            for row in all_subs:
                telegram_id = row[0]
                expires_at_str = row[1]
                starts_at_str = row[2] if len(row) > 2 else None
                try:
                    if not expires_at_str:
                        continue
                    expires_at = datetime.fromisoformat(expires_at_str)
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    
                    # Используем starts_at из запроса, если есть
                    if starts_at_str:
                        starts_at = datetime.fromisoformat(starts_at_str)
                        if starts_at.tzinfo is None:
                            starts_at = starts_at.replace(tzinfo=timezone.utc)
                    else:
                        # Если starts_at нет в запросе, получаем из sub_info
                        sub_info = await get_subscription_info(telegram_id)
                        if not sub_info:
                            continue
                        starts_at = sub_info.get('starts_at')
                        if starts_at and starts_at.tzinfo is None:
                            starts_at = starts_at.replace(tzinfo=timezone.utc)
                        if not starts_at:
                            continue
                    
                    # Получаем полную информацию о подписке для автопродления
                    sub_info = await get_subscription_info(telegram_id)
                    if not sub_info:
                        continue
                    
                    # Проверяем, что это подписка из бонусной недели
                    is_bonus_subscription = False
                    if starts_at:
                        is_bonus_subscription = bonus_week_start <= starts_at <= bonus_week_end
                        logger.info(f"🔍 Проверка бонусной подписки для {telegram_id}: starts_at={starts_at.isoformat()}, bonus_week_start={bonus_week_start.isoformat()}, bonus_week_end={bonus_week_end.isoformat()}, is_bonus={is_bonus_subscription}")
                    elif expires_at:
                        # Если starts_at нет, проверяем по expires_at
                        time_diff = (expires_at - bonus_week_end).total_seconds() / 60
                        is_bonus_subscription = expires_at <= bonus_week_end or (0 <= time_diff <= 2)
                        logger.info(f"🔍 Проверка бонусной подписки для {telegram_id} (без starts_at): expires_at={expires_at.isoformat()}, bonus_week_end={bonus_week_end.isoformat()}, time_diff={time_diff:.1f} мин, is_bonus={is_bonus_subscription}")
                    else:
                        logger.warning(f"⚠️ Нет starts_at и expires_at для пользователя {telegram_id}")
                    
                    if not is_bonus_subscription:
                        logger.info(f"⏭️ Пропуск пользователя {telegram_id}: не бонусная подписка")
                        continue
                    
                    logger.info(f"✅ Пользователь {telegram_id} имеет бонусную подписку, обрабатываем автопродление")
                    
                    # Получаем информацию об автопродлении
                    auto_renewal_enabled = sub_info.get('auto_renewal_enabled', False)
                    saved_payment_method_id = sub_info.get('saved_payment_method_id')
                    
                    if not auto_renewal_enabled or not saved_payment_method_id:
                        continue
                    
                    # Получаем информацию о попытках
                    attempts = await get_auto_renewal_attempts(telegram_id)
                    last_attempt_at = await get_last_auto_renewal_attempt_at(telegram_id)
                    
                    # Определяем, нужно ли выполнить попытку автопродления
                    should_attempt = False
                    attempt_number = 0
                    
                    if 0 <= time_since_bonus_end <= 3 and attempts == 0:
                        # Первая попытка: сразу после окончания бонусной недели
                        should_attempt = True
                        attempt_number = 1
                    elif last_attempt_at and attempts > 0 and attempts < 3:
                        # Проверяем, прошло ли 2 минуты с последней попытки
                        time_since_last_attempt = (now - last_attempt_at).total_seconds() / 60
                        if 2 <= time_since_last_attempt <= 5:  # С погрешностью ±3 минуты
                            should_attempt = True
                            attempt_number = attempts + 1
                    
                    if should_attempt:
                        auto_amount = get_production_subscription_price()
                        auto_duration = get_production_subscription_duration()
                        
                        # Выполняем попытку автопродления
                        success = await attempt_auto_renewal(telegram_id, saved_payment_method_id, auto_amount, auto_duration, attempt_number)
                        
                        if success:
                            # Успешно - меню уже обновлено в attempt_auto_renewal
                            logger.info(f"✅ Автопродление успешно для пользователя {telegram_id}, попытка {attempt_number}")
                        elif attempts + 1 >= 3:
                            # Все 3 попытки неудачны - бан и меню с "Оплатить доступ"
                            from db import set_auto_renewal, get_invite_link
                            from telegram_utils import revoke_invite_link
                            
                            await set_auto_renewal(telegram_id, False)
                            from db import _clear_cache
                            _clear_cache()
                            
                            # Отзываем ссылку пользователя
                            user_invite_link = await get_invite_link(telegram_id)
                            if user_invite_link:
                                await revoke_invite_link(user_invite_link)
                                logger.info(f"✅ Ссылка пользователя {telegram_id} отозвана из-за 3 неудачных попыток автопродления")
                            
                            # Баним пользователя в канале
                            try:
                                await bot.ban_chat_member(
                                    chat_id=CHANNEL_ID,
                                    user_id=telegram_id,
                                    until_date=None  # Бан навсегда
                                )
                                logger.info(f"✅ Пользователь {telegram_id} забанен в канале из-за 3 неудачных попыток автопродления")
                            except Exception as ban_error:
                                logger.warning(f"⚠️ Ошибка бана пользователя {telegram_id}: {ban_error}")
                            
                            # Показываем меню с кнопкой "Оплатить доступ"
                            menu = await get_main_menu_for_user(telegram_id)
                            await safe_send_message(
                                bot=bot,
                                chat_id=telegram_id,
                                text=(
                                    "⏰ <b>Ваш доступ истек</b>\n\n"
                                    "Все попытки автопродления не удались.\n"
                                    "Для возобновления доступа нажмите кнопку 💳 Получить доступ."
                                ),
                                parse_mode="HTML",
                                reply_markup=menu
                            )
                            logger.info(f"📧 Отправлено уведомление об истечении доступа пользователю {telegram_id} с меню 'Оплатить доступ'")
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка обработки пользователя {telegram_id}: {e}")
                    import traceback
                    traceback.print_exc()
            
        except Exception as e:
            logger.error(f"❌ Ошибка в фоновой задаче проверки перехода в продакшн режим: {e}")
            await asyncio.sleep(30)


async def check_bonus_week_ending_soon():
    """Проверяет окончание бонусной недели и отправляет уведомления за vremya_sms до окончания"""
    notified_users = set()  # Чтобы не отправлять несколько раз одному пользователю
    
    while True:
        try:
            # Проверяем каждую минуту для точного уведомления
            await asyncio.sleep(10)  # Уменьшено для более точного срабатывания
            
            if not is_bonus_week_active():
                # Если бонусная неделя не активна, очищаем кэш и продолжаем
                notified_users.clear()
                continue
            
            # Получаем всех пользователей с активными подписками
            from db import get_all_active_subscriptions
            active_subs = await get_all_active_subscriptions()
            
            now = datetime.now(timezone.utc)
            bonus_week_end = get_bonus_week_end()
            # Убеждаемся, что bonus_week_end имеет timezone
            if bonus_week_end.tzinfo is None:
                bonus_week_end = bonus_week_end.replace(tzinfo=timezone.utc)
            time_until_end = bonus_week_end - now
            minutes_until_end = time_until_end.total_seconds() / 60
            
            # Проверяем, нужно ли отправлять уведомление (за vremya_sms минут до окончания)
            # Используем погрешность ±0.5 минуты для точности (чтобы не пропустить и не отправить слишком рано)
            # Проверяем, что осталось от vremya_sms-0.5 до vremya_sms+0.5 минут
            if vremya_sms - 0.5 <= minutes_until_end <= vremya_sms + 0.5:
                logger.info(f"🔔 Время для уведомления о конце бонусной недели: minutes_until_end={minutes_until_end:.1f}, vremya_sms={vremya_sms}, bonus_week_end={bonus_week_end}, now={now}")
                for telegram_id, expires_at_str in active_subs:
                    if telegram_id in notified_users:
                        continue
                    
                    try:
                        # Проверяем, что подписка истекает до окончания бонусной недели (это бонусная подписка)
                        expires_at = datetime.fromisoformat(expires_at_str)
                        if expires_at <= bonus_week_end:
                            # Это подписка из бонусной недели
                            from db import is_auto_renewal_enabled
                            auto_renewal_enabled = await is_auto_renewal_enabled(telegram_id)
                            
                            if minutes_until_end >= 60:
                                hours = int(minutes_until_end // 60)
                                minutes = int(minutes_until_end % 60)
                                hours_text = f"{hours} час" if hours == 1 else (f"{hours} часа" if 2 <= hours <= 4 else f"{hours} часов")
                                if minutes > 0:
                                    minutes_text = f"{minutes} минут{'ы' if 2 <= minutes <= 4 else ''}"
                                    time_text = f"{hours_text} {minutes_text}"
                                else:
                                    time_text = hours_text
                            else:
                                time_text = f"{int(minutes_until_end)} минут{'ы' if 2 <= int(minutes_until_end) <= 4 else ''}"
                            
                            # Получаем время начала и окончания бонусной недели
                            from config import get_bonus_week_start
                            bonus_start = get_bonus_week_start()
                            # Убеждаемся, что datetime имеет timezone для правильного форматирования
                            if bonus_start.tzinfo is None:
                                bonus_start = bonus_start.replace(tzinfo=timezone.utc)
                            if bonus_week_end.tzinfo is None:
                                bonus_week_end = bonus_week_end.replace(tzinfo=timezone.utc)
                            bonus_start_str = format_datetime_moscow(bonus_start)
                            bonus_end_str = format_datetime_moscow(bonus_week_end)
                            
                            if auto_renewal_enabled:
                                notification_text = (
                                    f"🎉 <b>Бонусная неделя заканчивается!</b>\n\n"
                                    f"🕐 <b>Начало бонусной недели:</b> {bonus_start_str}\n"
                                    f"🕐 <b>Окончание бонусной недели:</b> {bonus_end_str}\n"
                                    f"⏰ <b>До окончания бонусной недели осталось:</b> {time_text}\n\n"
                                    f"⚠️ <b>Важно:</b> После окончания бонусной недели:\n"
                                    f"• Будет автоматически списана полная стоимость: <b>2990 рублей на 30 дней</b>\n"
                                    f"• Автопродление можно отключить в меню «Управление доступом» до окончания бонусной недели\n\n"
                                    f"⚙️ Вы можете отключить автопродление в меню «Управление доступом»."
                                )
                            else:
                                notification_text = (
                                    f"🎉 <b>Бонусная неделя заканчивается!</b>\n\n"
                                    f"🕐 <b>Начало бонусной недели:</b> {bonus_start_str}\n"
                                    f"🕐 <b>Окончание бонусной недели:</b> {bonus_end_str}\n"
                                    f"⏰ <b>До окончания бонусной недели осталось:</b> {time_text}\n\n"
                                    f"⚠️ <b>Важно:</b> После окончания бонусной недели:\n"
                                    f"• Ваш доступ в канал закончится\n"
                                    f"• Вы будете удалены из канала\n"
                                    f"• Для возобновления доступа необходимо оплатить заново"
                                )
                            
                            await safe_send_message(
                                bot=bot,
                                chat_id=telegram_id,
                                text=notification_text,
                                parse_mode="HTML"
                            )
                            notified_users.add(telegram_id)
                            logger.info(f"✅ Отправлено уведомление об окончании бонусной недели пользователю {telegram_id}")
                    except Exception as e:
                        logger.error(f"❌ Ошибка отправки уведомления пользователю {telegram_id}: {e}")
            
            # Очищаем кэш, если бонусная неделя закончилась
            if minutes_until_end < 0:
                notified_users.clear()
                    
        except Exception as e:
            logger.error(f"❌ Ошибка в фоновой задаче проверки окончания бонусной недели: {e}")
            await asyncio.sleep(10)  # Уменьшено для более точного срабатывания


async def check_expired_subscriptions():
    """Проверяет истекшие подписки и выполняет автопродление или отправляет ссылку на оплату"""
    processed_users = {}  # {telegram_id: timestamp} - чтобы не отправлять несколько раз одному пользователю в течение короткого времени
    
    while True:
        try:
            await asyncio.sleep(CHECK_EXPIRED_SUBSCRIPTIONS_INTERVAL_SECONDS)
            
            # ВАЖНО: Очищаем processed_users от записей старше 5 минут (чтобы можно было повторить попытку)
            # НО: НЕ удаляем пользователей, для которых уже было отправлено уведомление об истечении доступа
            # Это предотвращает повторную обработку и возможные нежелательные действия (например, автоматическую отправку /start)
            now_check = datetime.now(timezone.utc)
            expired_processed = []
            for uid, ts in processed_users.items():
                # Проверяем, было ли уже отправлено уведомление об истечении доступа
                from db import get_subscription_expired_notified
                already_notified = await get_subscription_expired_notified(uid)
                
                # НЕ удаляем пользователей, для которых уже было отправлено уведомление об истечении
                if already_notified:
                    logger.debug(f"🔒 Пользователь {uid} не будет удален из processed_users (уведомление об истечении уже отправлено)")
                    continue
                
                # Убеждаемся, что ts имеет timezone
                if isinstance(ts, str):
                    ts_dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                else:
                    ts_dt = ts
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                if (now_check - ts_dt).total_seconds() > 300:
                    expired_processed.append(uid)
            for uid in expired_processed:
                del processed_users[uid]
                logger.info(f"🔄 Удален пользователь {uid} из processed_users (прошло более 5 минут)")
            
            # Проверяем подписки, которые истекли
            expired_subs = await get_expired_subscriptions()
            
            # ВАЖНО: Бонусные подписки обрабатываются в check_bonus_week_transition_to_production()
            # Эта функция обрабатывает только обычные истекшие подписки
            
            logger.info(f"🔍 Проверка подписок для автопродления: найдено {len(expired_subs)} подписок (только истекшие, бонусные обрабатываются отдельно)")
            
            for row in expired_subs:
                telegram_id = row[0]
                expires_at_str = row[1]
                auto_renewal_enabled = bool(row[2]) if len(row) > 2 else False
                saved_payment_method_id = row[3] if len(row) > 3 and row[3] else None
                starts_at_str = row[4] if len(row) > 4 and row[4] else None  # Время начала подписки
                
                logger.info(f"📋 Обработка подписки пользователя {telegram_id}: expires_at={expires_at_str}, starts_at={starts_at_str}, auto_renewal={auto_renewal_enabled}, saved_method={bool(saved_payment_method_id)}")
                
                # КРИТИЧНО: Проверяем, было ли уже отправлено уведомление об истечении доступа
                # Если да, НЕ обрабатываем пользователя повторно, чтобы избежать нежелательных действий
                from db import get_subscription_expired_notified
                already_notified_expired = await get_subscription_expired_notified(telegram_id)
                if already_notified_expired:
                    logger.info(f"🔒 Пользователь {telegram_id} уже получил уведомление об истечении доступа - пропускаем обработку (защита от повторных действий)")
                    continue
                
                # Проверяем, был ли пользователь обработан недавно (в течение последних 2 минут)
                if telegram_id in processed_users:
                    time_since_processed = (now - processed_users[telegram_id]).total_seconds()
                    if time_since_processed < 120:  # 2 минуты
                        logger.info(f"⏭️ Пользователь {telegram_id} уже обработан {time_since_processed:.0f} секунд назад, пропускаем")
                        continue
                    # НЕ удаляем из processed_users автоматически - пусть остается до очистки выше
                    logger.info(f"🔄 Пользователь {telegram_id} был обработан {time_since_processed:.0f} секунд назад, продолжаем обработку")
                    
                try:
                    expires_at = datetime.fromisoformat(expires_at_str)
                    # Убеждаемся, что expires_at имеет timezone
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    now = datetime.now(timezone.utc)
                    
                    logger.info(f"⏰ Пользователь {telegram_id}: expires_at={expires_at}, now={now}, разница={(now - expires_at).total_seconds()} секунд")
                    
                    # КРИТИЧЕСКАЯ ПРОВЕРКА: Определяем, нужно ли выполнять автопродление
                    # Автопродление нужно выполнять если:
                    # 1. Подписка уже истекла (expires_at <= now) - для обычных подписок
                    # 2. Бонусная неделя закончилась и это бонусная подписка - даже если подписка еще не истекла
                    # Сначала получаем информацию о бонусной неделе
                    from config import get_bonus_week_start, get_bonus_week_end
                    bonus_week_start_check = get_bonus_week_start()
                    bonus_week_end_check = get_bonus_week_end()
                    if bonus_week_start_check.tzinfo is None:
                        bonus_week_start_check = bonus_week_start_check.replace(tzinfo=timezone.utc)
                    if bonus_week_end_check.tzinfo is None:
                        bonus_week_end_check = bonus_week_end_check.replace(tzinfo=timezone.utc)
                    
                    # Определяем, является ли это бонусная подписка
                    is_bonus_subscription_check = False
                    if starts_at_str:
                        try:
                            starts_at_check = datetime.fromisoformat(starts_at_str)
                            if starts_at_check.tzinfo is None:
                                starts_at_check = starts_at_check.replace(tzinfo=timezone.utc)
                            is_bonus_subscription_check = bonus_week_start_check <= starts_at_check <= bonus_week_end_check
                        except Exception:
                            pass
                    if not is_bonus_subscription_check and expires_at:
                        time_diff_check = (expires_at - bonus_week_end_check).total_seconds() / 60
                        is_bonus_subscription_check = expires_at <= bonus_week_end_check or (0 <= time_diff_check <= 2)
                    
                    # Проверяем, закончилась ли бонусная неделя
                    bonus_week_ended_check = not is_bonus_week_active()
                    if not bonus_week_ended_check and bonus_week_end_check:
                        time_since_bonus_end_check = (now - bonus_week_end_check).total_seconds() / 60
                        if time_since_bonus_end_check > 0:
                            bonus_week_ended_check = True
                    
                    # КРИТИЧЕСКАЯ ПРОВЕРКА: Если это бонусная подписка, пропускаем её
                    # Бонусные подписки обрабатываются ТОЛЬКО в check_bonus_week_transition_to_production()
                    # Это предотвращает конфликт между двумя системами автопродления
                    if is_bonus_subscription_check:
                        logger.info(f"⏭️ Пропуск бонусной подписки пользователя {telegram_id} - обрабатывается в check_bonus_week_transition_to_production()")
                        continue
                    
                    # Определяем, нужно ли выполнять автопродление
                    should_do_auto_renewal = False
                    if expires_at <= now:
                        # Подписка истекла - нужно автопродление (только для обычных подписок)
                        should_do_auto_renewal = True
                        logger.info(f"🔍 Подписка пользователя {telegram_id} истекла (expires_at={expires_at}, now={now}) - нужно автопродление")
                    
                    if should_do_auto_renewal:
                        auto_payment_failed = False
                        auto_payment_succeeded = False  # Флаг успешного автопродления
                        
                        # Проверяем, является ли это подписка из бонусной недели
                        from config import get_bonus_week_start
                        bonus_week_start = get_bonus_week_start()
                        bonus_week_end = get_bonus_week_end()
                        # Убеждаемся, что bonus_week_start и bonus_week_end имеют timezone
                        if bonus_week_start.tzinfo is None:
                            bonus_week_start = bonus_week_start.replace(tzinfo=timezone.utc)
                        if bonus_week_end.tzinfo is None:
                            bonus_week_end = bonus_week_end.replace(tzinfo=timezone.utc)
                        
                        # Определяем, является ли это подписка из бонусной недели
                        # Вариант 1: Проверяем по starts_at (если доступно) - подписка была создана во время бонусной недели
                        is_bonus_subscription = False
                        if starts_at_str:
                            try:
                                starts_at = datetime.fromisoformat(starts_at_str)
                                if starts_at.tzinfo is None:
                                    starts_at = starts_at.replace(tzinfo=timezone.utc)
                                # Подписка из бонусной недели, если она была создана во время бонусной недели
                                is_bonus_subscription = bonus_week_start <= starts_at <= bonus_week_end
                                logger.info(f"🔍 Проверка по starts_at: starts_at={starts_at}, bonus_week_start={bonus_week_start}, bonus_week_end={bonus_week_end}, is_bonus={is_bonus_subscription}")
                            except Exception as e:
                                logger.warning(f"⚠️ Ошибка парсинга starts_at для пользователя {telegram_id}: {e}")
                        
                        # Вариант 2: Если starts_at недоступно, проверяем по expires_at
                        # Подписка из бонусной недели, если она истекает до или в момент окончания бонусной недели
                        if not is_bonus_subscription and expires_at:
                            # Подписка из бонусной недели, если она истекает до или в момент окончания бонусной недели
                            # (с учетом погрешности в 2 минуты для подписок, созданных в конце бонусной недели)
                            time_diff = (expires_at - bonus_week_end).total_seconds() / 60
                            is_bonus_subscription = expires_at <= bonus_week_end or (0 <= time_diff <= 2)
                            logger.info(f"🔍 Проверка по expires_at: expires_at={expires_at}, bonus_week_end={bonus_week_end}, time_diff={time_diff:.1f} мин, is_bonus={is_bonus_subscription}")
                        
                        bonus_week_ended = not is_bonus_week_active()
                        
                        # КРИТИЧЕСКАЯ ПРОВЕРКА: Если подписка истекает точно в момент окончания бонусной недели,
                        # и бонусная неделя уже закончилась, это тоже считается окончанием бонусной недели
                        # ВАЖНО: Также проверяем, что текущее время уже после окончания бонусной недели
                        if not bonus_week_ended and expires_at and bonus_week_end:
                            # Проверяем, истекла ли подписка в момент или после окончания бонусной недели
                            time_diff_from_bonus_end = (expires_at - bonus_week_end).total_seconds() / 60
                            time_since_bonus_end = (now - bonus_week_end).total_seconds() / 60
                            # Если бонусная неделя закончилась (now > bonus_week_end) и подписка истекает в момент или после окончания
                            if time_since_bonus_end > 0 and (-1 <= time_diff_from_bonus_end <= 1):
                                # Считаем, что бонусная неделя закончилась для этого пользователя
                                bonus_week_ended = True
                                logger.info(f"🔍 Подписка пользователя {telegram_id} истекает в момент окончания бонусной недели (разница: {time_diff_from_bonus_end:.1f} мин, прошло с окончания: {time_since_bonus_end:.1f} мин) - считаем, что бонусная неделя закончилась")
                        
                        # ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА: Если текущее время уже после окончания бонусной недели, считаем что она закончилась
                        if not bonus_week_ended and bonus_week_end:
                            time_since_bonus_end = (now - bonus_week_end).total_seconds() / 60
                            if time_since_bonus_end > 0:
                                bonus_week_ended = True
                                logger.info(f"🔍 Бонусная неделя закончилась {time_since_bonus_end:.1f} минут назад для пользователя {telegram_id} - считаем, что бонусная неделя закончилась")
                        
                        # КРИТИЧЕСКАЯ ПРОВЕРКА: Если это бонусная подписка и бонусная неделя закончилась,
                        # но подписка еще не истекла (expires_at > now), все равно считаем что нужно автопродление
                        # Это важно для случаев, когда подписка истекает после окончания бонусной недели
                        if is_bonus_subscription and bonus_week_ended and expires_at > now:
                            logger.info(f"🔍 КРИТИЧЕСКАЯ СИТУАЦИЯ: Бонусная неделя закончилась, но подписка еще активна (expires_at={expires_at}, now={now}) - автопродление должно сработать")
                        
                        logger.info(f"🔍 Пользователь {telegram_id}: bonus_week_ended={bonus_week_ended}, is_bonus_subscription={is_bonus_subscription}, starts_at={starts_at_str}, expires_at={expires_at}, bonus_week_start={bonus_week_start}, bonus_week_end={bonus_week_end}, now={now}, auto_renewal={auto_renewal_enabled}, saved_method={bool(saved_payment_method_id)}")
                        
                        # ВАЖНО: Проверяем, что у пользователя есть хотя бы один успешный платеж в БД
                        # Это предотвращает автопродление для пользователей, которые никогда не платили
                        async with aiosqlite.connect(DB_PATH) as db_check_payment:
                            cursor_payment = await db_check_payment.execute(
                                "SELECT COUNT(*) FROM payments WHERE telegram_id = ? AND status = 'succeeded'",
                                (telegram_id,)
                            )
                            row_payment = await cursor_payment.fetchone()
                            has_successful_payment = row_payment and row_payment[0] and row_payment[0] > 0
                        
                        if not has_successful_payment:
                            logger.warning(f"⚠️ Пропуск автопродления для пользователя {telegram_id}: нет успешных платежей в БД (пользователь никогда не платил)")
                            # Не выполняем автопродление, но продолжаем обработку для бана/уведомлений
                            auto_payment_failed = True
                        # Проверяем, включено ли автопродление и есть ли сохраненный способ оплаты
                        elif auto_renewal_enabled and saved_payment_method_id:
                            # Пытаемся выполнить автоматическое списание
                            try:
                                from payments import create_auto_payment, get_payment_status
                                from db import activate_subscription_days, save_payment, update_payment_status
                                
                                CUSTOMER_EMAIL = os.getenv("PAYMENT_CUSTOMER_EMAIL", "test@example.com")
                                
                                # Определяем цену и длительность для автопродления
                                # КРИТИЧЕСКИ ВАЖНО: Автопродление для бонусных подписок должно срабатывать ТОЛЬКО при окончании бонусной недели
                                # Если бонусная неделя еще активна и это бонусная подписка, НЕ делаем автопродление - ждем окончания бонусной недели
                                if bonus_week_ended and is_bonus_subscription:
                                    # Бонусная неделя закончилась и это была подписка из бонусной недели - используем продакшн цены
                                    auto_amount = get_production_subscription_price()
                                    auto_duration = get_production_subscription_duration()
                                    logger.info(f"🔄 Бонусная неделя закончилась для пользователя {telegram_id}, используем продакшн цены: {auto_amount} руб, {auto_duration} дней")
                                elif is_bonus_week_active() and is_bonus_subscription:
                                    # Бонусная неделя еще активна и это бонусная подписка - НЕ делаем автопродление, ждем окончания бонусной недели
                                    logger.info(f"⏸️ Бонусная неделя еще активна для пользователя {telegram_id}, автопродление будет выполнено при окончании бонусной недели (expires_at={expires_at}, bonus_week_end={bonus_week_end})")
                                    auto_payment_failed = True  # Помечаем как неудачное, чтобы не отправлять уведомление
                                    continue  # Пропускаем автопродление
                                elif is_bonus_week_active():
                                    # Бонусная неделя активна, но это не бонусная подписка (продакшн подписка) - используем продакшн цены
                                    auto_amount = get_production_subscription_price()
                                    auto_duration = get_production_subscription_duration()
                                    logger.info(f"💼 Продакшн подписка во время бонусной недели для пользователя {telegram_id}, используем продакшн цены: {auto_amount} руб, {auto_duration} дней")
                                else:
                                    # Обычный продакшн режим
                                    auto_amount = get_production_subscription_price()
                                    auto_duration = get_production_subscription_duration()
                                    logger.info(f"💼 Продакшн режим для пользователя {telegram_id}, используем продакшн цены: {auto_amount} руб, {auto_duration} дней")
                                
                                # КРИТИЧЕСКАЯ ПРОВЕРКА: Проверяем, не выполняли ли мы уже автопродление для этого пользователя
                                # после окончания бонусной недели (чтобы выполнить только ОДИН РАЗ)
                                auto_payment_bonus_key = f"auto_payment_bonus_ended_{telegram_id}"
                                if await already_processed(auto_payment_bonus_key):
                                    logger.warning(f"⚠️ Автопродление после окончания бонусной недели уже было выполнено для пользователя {telegram_id} - пропускаем")
                                    continue
                                
                                # Создаем автоматический платеж
                                payment_id, payment_status = create_auto_payment(
                                    amount_rub=auto_amount,
                                    description=f"Автопродление доступа на канал ({format_subscription_duration(auto_duration)})",
                            customer_email=CUSTOMER_EMAIL,
                            telegram_user_id=telegram_id,
                                    payment_method_id=saved_payment_method_id,
                        )
                        
                        # Сохраняем платеж
                                await save_payment(telegram_id, payment_id, status=payment_status)
                                
                                # Помечаем, что автопродление после окончания бонусной недели было выполнено (ОДИН РАЗ)
                                await mark_processed(auto_payment_bonus_key)
                                logger.info(f"✅ Автопродление после окончания бонусной недели помечено как выполненное для пользователя {telegram_id}")
                                
                                # Если платеж сразу не succeeded, ждем webhook или проверяем статус
                                if payment_status != "succeeded":
                                    logger.info(f"ℹ️ Автоплатеж {payment_id} для пользователя {telegram_id} в статусе {payment_status}, ждем webhook или повторную проверку.")
                                    # Даем немного времени на обработку webhook
                                    await asyncio.sleep(3)
                                    # Проверяем статус еще раз
                                    refreshed_status = get_payment_status(payment_id)
                                    await update_payment_status(payment_id, refreshed_status)
                                    payment_status = refreshed_status  # Обновляем payment_status для дальнейшей проверки
                                    if refreshed_status != "succeeded":
                                        auto_payment_failed = True
                                        logger.warning(f"⚠️ Автоплатеж {payment_id} для пользователя {telegram_id} не завершился успешно после ожидания, статус: {refreshed_status}")
                                    else:
                                        logger.info(f"✅ Автоплатеж {payment_id} для пользователя {telegram_id} успешно завершен после ожидания.")
                                
                                # КРИТИЧЕСКАЯ ПРОВЕРКА: Убеждаемся, что платеж действительно успешен
                                # Проверяем статус платежа из API YooKassa перед обработкой
                                final_payment_status = payment_status  # Используем актуальный статус
                                try:
                                    from yookassa import Payment
                                    payment_api_check = Payment.find_one(payment_id)
                                    final_payment_status = payment_api_check.status  # Используем статус из API как финальный
                                    if final_payment_status != "succeeded":
                                        logger.warning(f"⚠️ Автопродление не удалось для пользователя {telegram_id}: платеж {payment_id} не имеет статус 'succeeded' в API (статус: {final_payment_status})")
                                        auto_payment_failed = True
                                        # НЕ делаем continue - обработаем ниже недостаточность средств
                                except Exception as api_check_error:
                                    logger.error(f"❌ Ошибка проверки статуса платежа {payment_id} в API: {api_check_error}")
                                    auto_payment_failed = True
                                    # НЕ делаем continue - обработаем ниже недостаточность средств
                                
                                # Если платеж успешен (сразу или после ожидания)
                                if final_payment_status == "succeeded" and not auto_payment_failed:
                                    # ВАЖНО: Проверяем, что платеж действительно существует в БД и имеет статус "succeeded"
                                    # Это предотвращает отправку уведомлений о несуществующих платежах
                                    async with aiosqlite.connect(DB_PATH) as db_check_payment:
                                        cursor_payment = await db_check_payment.execute(
                                            "SELECT payment_id, status, created_at FROM payments WHERE payment_id = ?",
                                            (payment_id,)
                                        )
                                        row_payment = await cursor_payment.fetchone()
                                        
                                        if not row_payment or row_payment[1] != "succeeded":
                                            logger.warning(f"⚠️ Пропуск автопродления для пользователя {telegram_id}: платеж {payment_id} не найден в БД или не имеет статус 'succeeded'")
                                            auto_payment_failed = True
                                            continue
                                        
                                        # ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА: Проверяем, что платеж был создан недавно (в течение последних 10 минут)
                                        # Это предотвращает обработку старых платежей
                                        if row_payment[2]:
                                            try:
                                                payment_created_at = datetime.fromisoformat(row_payment[2])
                                                if payment_created_at.tzinfo is None:
                                                    payment_created_at = payment_created_at.replace(tzinfo=timezone.utc)
                                                time_since_creation = (datetime.now(timezone.utc) - payment_created_at).total_seconds() / 60
                                                if time_since_creation > 10:
                                                    logger.warning(f"⚠️ Пропуск автопродления для пользователя {telegram_id}: платеж {payment_id} был создан {time_since_creation:.1f} минут назад (слишком старый)")
                                                    auto_payment_failed = True
                                                    continue
                                            except Exception as time_check_error:
                                                logger.warning(f"⚠️ Ошибка проверки времени создания платежа {payment_id}: {time_check_error}")
                                                # Продолжаем обработку, если не удалось проверить время
                                    
                                    # ВАЖНО: Проверяем, что у пользователя есть хотя бы один успешный платеж в БД (кроме текущего автоплатежа)
                                    # Это гарантирует, что мы не отправляем уведомление пользователям, которые никогда не платили
                                    async with aiosqlite.connect(DB_PATH) as db_check:
                                        cursor = await db_check.execute(
                                            "SELECT COUNT(*) FROM payments WHERE telegram_id = ? AND status = 'succeeded' AND payment_id != ?",
                                            (telegram_id, payment_id)
                                        )
                                        row = await cursor.fetchone()
                                        has_previous_successful_payment = row and row[0] and row[0] > 0
                                    
                                    if not has_previous_successful_payment:
                                        logger.warning(f"⚠️ Пропуск автопродления для пользователя {telegram_id}: нет предыдущих успешных платежей в БД (пользователь никогда не платил)")
                                        auto_payment_failed = True
                                        continue
                                    
                                    # КРИТИЧЕСКАЯ ПРОВЕРКА: Проверяем, что это НЕ повторная обработка того же платежа
                                    # Используем processed_payments для отслеживания уже обработанных автоплатежей
                                    auto_payment_key = f"auto_payment_{payment_id}_{telegram_id}"
                                    if await already_processed(auto_payment_key):
                                        logger.warning(f"⚠️ КРИТИЧЕСКИЙ БАГ ПРЕДОТВРАЩЕН: Автоплатеж {payment_id} для пользователя {telegram_id} уже был обработан ранее - пропускаем повторную обработку")
                                        auto_payment_failed = True
                                        continue
                                    
                                    # Помечаем автоплатеж как обработанный ДО активации подписки
                                    await mark_processed(auto_payment_key)
                                    logger.info(f"✅ Автоплатеж {payment_id} помечен как обработанный для пользователя {telegram_id}")
                                    
                                    # Используем ту же длительность, что и для платежа
                                    await activate_subscription_days(telegram_id, days=auto_duration)
                                    auto_payment_succeeded = True  # Помечаем, что автопродление успешно
                                    
                                    # Разбаниваем пользователя, если был забанен
                                    try:
                                        await bot.unban_chat_member(
                                            chat_id=CHANNEL_ID,
                                            user_id=telegram_id,
                                            only_if_banned=True
                                        )
                                    except Exception:
                                        pass
                                    
                                    # Отправляем уведомление об успешном автопродлении
                                    # Правильное склонение для рублей (используем цену из автопродления)
                                    amount_float = float(auto_amount)
                                    if amount_float == 1:
                                        ruble_text = "рубль"
                                    elif 2 <= amount_float <= 4 or (amount_float % 10 >= 2 and amount_float % 10 <= 4 and amount_float % 100 not in [12, 13, 14]):
                                        ruble_text = "рубля"
                                    else:
                                        ruble_text = "рублей"
                                    
                                    # Определяем тип способа оплаты для сообщения
                                    payment_method_text = "с вашего способа оплаты"
                                    try:
                                        from yookassa import Payment
                                        # Пытаемся получить информацию о платеже для определения типа
                                        payment_info = Payment.find_one(payment_id)
                                        if hasattr(payment_info, 'payment_method') and payment_info.payment_method:
                                            pm = payment_info.payment_method
                                            pm_type = None
                                            if hasattr(pm, 'type'):
                                                pm_type = pm.type
                                            elif isinstance(pm, dict) and 'type' in pm:
                                                pm_type = pm['type']
                                            if pm_type:
                                                pm_type_lower = pm_type.lower()
                                                if pm_type_lower == 'sbp':
                                                    payment_method_text = "через СБП"
                                                elif pm_type_lower in ['sberbank', 'sberpay']:
                                                    payment_method_text = "через SberPay"
                                                elif pm_type_lower in ['bank_card', 'card']:
                                                    payment_method_text = "с вашей карты"
                                    except Exception:
                                        pass  # Если не удалось определить, используем универсальный текст
                                    
                                    # КРИТИЧЕСКАЯ ПРОВЕРКА: Проверяем, не отправляли ли мы уже уведомление об этом автопродлении
                                    # Используем уникальный ключ для каждого автопродления
                                    auto_renewal_notification_key = f"auto_renewal_notification_{payment_id}_{telegram_id}"
                                    
                                    # ДОПОЛНИТЕЛЬНАЯ ЗАЩИТА: Проверяем, не было ли уже автопродление для этого пользователя в последние 2 минуты
                                    # Это предотвращает дублирование при параллельной обработке
                                    auto_renewal_user_key = f"auto_renewal_user_{telegram_id}"
                                    if await already_processed(auto_renewal_user_key):
                                        logger.warning(f"⚠️ Автопродление для пользователя {telegram_id} уже обрабатывалось недавно - пропускаем дублирование")
                                        continue
                                    
                                    # ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА: Убеждаемся, что платеж действительно был создан в рамках автопродления
                                    # Проверяем, что платеж был создан недавно (в течение последних 5 минут)
                                    # Это гарантирует, что мы не обрабатываем старые платежи
                                    payment_created_recently = False
                                    try:
                                        async with aiosqlite.connect(DB_PATH) as db_check_time:
                                            cursor_time = await db_check_time.execute(
                                                "SELECT created_at FROM payments WHERE payment_id = ?",
                                                (payment_id,)
                                            )
                                            row_time = await cursor_time.fetchone()
                                            if row_time and row_time[0]:
                                                payment_created_at = datetime.fromisoformat(row_time[0])
                                                if payment_created_at.tzinfo is None:
                                                    payment_created_at = payment_created_at.replace(tzinfo=timezone.utc)
                                                time_since_creation = (datetime.now(timezone.utc) - payment_created_at).total_seconds() / 60
                                                if time_since_creation <= 5:  # Платеж создан не более 5 минут назад
                                                    payment_created_recently = True
                                    except Exception as time_check_error:
                                        logger.warning(f"⚠️ Ошибка проверки времени создания платежа {payment_id}: {time_check_error}")
                                        # Если не удалось проверить время, считаем что платеж новый
                                        payment_created_recently = True
                                    
                                    if not payment_created_recently:
                                        logger.warning(f"⚠️ Пропуск отправки уведомления об автопродлении: платеж {payment_id} был создан более 5 минут назад (возможно, это старый платеж)")
                                        continue
                                    
                                    # КРИТИЧЕСКАЯ ПРОВЕРКА: Проверяем, не отправляли ли мы уже уведомление
                                    # ВАЖНО: Проверяем ДО отправки, чтобы предотвратить дублирование
                                    if await already_processed(auto_renewal_notification_key):
                                        logger.warning(f"⚠️ Уведомление об автопродлении для платежа {payment_id} уже было отправлено пользователю {telegram_id} - пропускаем")
                                        continue
                                    
                                    # КРИТИЧЕСКИ ВАЖНО: Помечаем уведомление как отправленное ДО отправки
                                    # Это предотвращает race condition и дублирование уведомлений
                                    await mark_processed(auto_renewal_notification_key)
                                    await mark_processed(auto_renewal_user_key)  # Помечаем пользователя как обработанного
                                    logger.info(f"✅ Уведомление об автопродлении помечено как отправленное ДО отправки для платежа {payment_id}, пользователь {telegram_id}")
                                    
                                    # Отправляем уведомление об успешном автопродлении
                                    await safe_send_message(
                                        bot=bot,
                                        chat_id=telegram_id,
                                        text="✅ Доступ автоматически продлен!\n\n"
                                            f"Списано {auto_amount} {ruble_text} {payment_method_text}.\n"
                                            f"Доступ продлен на {format_subscription_duration(auto_duration)}.\n\n"
                                            "Спасибо за использование автопродления!"
                                    )
                                    logger.info(f"✅ Уведомление об автопродлении отправлено пользователю {telegram_id} для платежа {payment_id}")
                                    logger.info(f"✅ Автопродление выполнено для пользователя {telegram_id}, payment_id: {payment_id}")
                                else:
                                    # Платеж не прошел - проверяем причину и обрабатываем
                                    auto_payment_failed = True
                                    logger.warning(f"⚠️ Автопродление не удалось для пользователя {telegram_id}, payment_id: {payment_id}, final_status: {final_payment_status}")
                                    
                                    # Проверяем детали платежа для определения причины отказа (недостаточность средств)
                                    insufficient_funds = False
                                    try:
                                        # Используем payment_api_check если он уже был получен, иначе получаем заново
                                        payment_obj = payment_api_check if 'payment_api_check' in locals() else Payment.find_one(payment_id)
                                        if hasattr(payment_obj, 'cancellation_details') and payment_obj.cancellation_details:
                                            cd = payment_obj.cancellation_details
                                            reason = None
                                            party = None
                                            if hasattr(cd, 'reason'):
                                                reason = cd.reason
                                            elif isinstance(cd, dict):
                                                reason = cd.get('reason')
                                            if hasattr(cd, 'party'):
                                                party = cd.party
                                            elif isinstance(cd, dict):
                                                party = cd.get('party')
                                            
                                            # Проверяем, является ли это ошибка недостаточности средств
                                            if reason and ('insufficient_funds' in str(reason).lower() or 'not_enough_money' in str(reason).lower() or 'недостаточно' in str(reason).lower()):
                                                insufficient_funds = True
                                                logger.info(f"💰 Обнаружена недостаточность средств для пользователя {telegram_id}, payment_id: {payment_id}, reason: {reason}")
                                    except Exception as payment_check_error:
                                        logger.warning(f"⚠️ Ошибка проверки деталей платежа {payment_id}: {payment_check_error}")
                                    
                                    # Автоматически отключаем автопродление при неудаче
                                    from db import set_auto_renewal
                                    await set_auto_renewal(telegram_id, False)
                                    _clear_cache()
                                    logger.info(f"🔄 Автопродление автоматически отключено для пользователя {telegram_id} из-за неудачного автоплатежа")
                                    
                                    # Отзываем ссылку пользователя
                                    from db import get_invite_link
                                    user_invite_link = await get_invite_link(telegram_id)
                                    if user_invite_link:
                                        await revoke_invite_link(user_invite_link)
                                        logger.info(f"✅ Ссылка пользователя {telegram_id} отозвана из-за неудачного автопродления")
                                    
                                    # Баним пользователя в канале
                                    try:
                                        await bot.ban_chat_member(
                                            chat_id=CHANNEL_ID,
                                            user_id=telegram_id,
                                            until_date=None  # Бан навсегда
                                        )
                                        logger.info(f"✅ Пользователь {telegram_id} забанен в канале из-за неудачного автопродления")
                                    except Exception as ban_error:
                                        logger.warning(f"⚠️ Ошибка бана пользователя {telegram_id}: {ban_error}")
                                    
                                    # КРИТИЧЕСКИ ВАЖНО: Проверяем, является ли это бонусная подписка
                                    # Если да, НЕ отправляем эти уведомления - они будут отправлены в check_bonus_week_transition_to_production()
                                    from config import get_bonus_week_start, get_bonus_week_end
                                    bonus_week_start_check = get_bonus_week_start()
                                    bonus_week_end_check = get_bonus_week_end()
                                    if bonus_week_start_check.tzinfo is None:
                                        bonus_week_start_check = bonus_week_start_check.replace(tzinfo=timezone.utc)
                                    if bonus_week_end_check.tzinfo is None:
                                        bonus_week_end_check = bonus_week_end_check.replace(tzinfo=timezone.utc)
                                    
                                    is_bonus_subscription_check = False
                                    if starts_at_str:
                                        try:
                                            starts_at_check = datetime.fromisoformat(starts_at_str)
                                            if starts_at_check.tzinfo is None:
                                                starts_at_check = starts_at_check.replace(tzinfo=timezone.utc)
                                            is_bonus_subscription_check = bonus_week_start_check <= starts_at_check <= bonus_week_end_check
                                        except Exception:
                                            pass
                                    
                                    # Если это бонусная подписка, НЕ отправляем уведомления - они будут отправлены в check_bonus_week_transition_to_production()
                                    if is_bonus_subscription_check:
                                        logger.info(f"⏭️ Пропуск уведомлений для бонусной подписки пользователя {telegram_id} - они будут отправлены в check_bonus_week_transition_to_production()")
                                    else:
                                        # Отправляем сообщение о недостаточности средств (если это причина) или об общей ошибке
                                        menu = await get_main_menu_for_user(telegram_id)
                                        if insufficient_funds:
                                            await safe_send_message(
                                                bot=bot,
                                                chat_id=telegram_id,
                                                text=(
                                                    "⚠️ <b>У вас недостаточно средств</b>\n\n"
                                                    "На вашей карте недостаточно средств для автопродления подписки.\n"
                                                    "Автопродление и доступ будут закрыты.\n\n"
                                                    "Для возобновления доступа используйте кнопку 💳 Получить доступ."
                                                ),
                                                parse_mode="HTML",
                                                reply_markup=menu
                                            )
                                            logger.warning(f"💰 Недостаточность средств для пользователя {telegram_id}, payment_id: {payment_id}")
                                        else:
                                            await safe_send_message(
                                                bot=bot,
                                                chat_id=telegram_id,
                                                text=(
                                                    "⚠️ <b>Автопродление не удалось</b>\n\n"
                                                    "Не удалось списать средства с вашего способа оплаты.\n"
                                                    "Автопродление автоматически отключено.\n\n"
                                                    "Для продления доступа используйте кнопку 💳 Получить доступ."
                                                ),
                                                parse_mode="HTML",
                                                reply_markup=menu
                                            )
                                        
                                        # Отправляем уведомление об истечении доступа с обновленным меню
                                        from db import get_subscription_expired_notified, set_subscription_expired_notified
                                        already_notified_expired = await get_subscription_expired_notified(telegram_id)
                                        if not already_notified_expired:
                                            await safe_send_message(
                                                bot=bot,
                                                chat_id=telegram_id,
                                                text="⏰ <b>Ваш доступ истек</b>\n\n"
                                                    "Для продления доступа нажмите кнопку 💳 Получить доступ.",
                                                parse_mode="HTML",
                                                reply_markup=menu
                                            )
                                            await set_subscription_expired_notified(telegram_id, True)
                                            logger.info(f"📧 Отправлено уведомление об истечении доступа пользователю {telegram_id} с обновленным меню")
                                    
                            except Exception as auto_payment_error:
                                logger.error(f"❌ Ошибка автоматического списания для пользователя {telegram_id}: {auto_payment_error}")
                                auto_payment_failed = True
                                
                                # Автоматически отключаем автопродление при ошибке
                                from db import set_auto_renewal
                                await set_auto_renewal(telegram_id, False)
                                logger.info(f"🔄 Автопродление автоматически отключено для пользователя {telegram_id} из-за ошибки автоплатежа")
                        
                        # Если автопродление не включено или не удалось, баним и отправляем ссылку на оплату
                        # НО: если автопродление успешно (auto_payment_succeeded = True), НЕ баним пользователя
                        # ВАЖНО: В бонусной неделе, если автопродление отключено, не баним до окончания бонусной недели
                        if not auto_renewal_enabled or not saved_payment_method_id or auto_payment_failed:
                            logger.info(f"🚫 Автопродление не работает для пользователя {telegram_id}: auto_renewal={auto_renewal_enabled}, saved_method={bool(saved_payment_method_id)}, failed={auto_payment_failed}")
                            
                            # Проверяем, активна ли бонусная неделя и является ли это подписка из бонусной недели
                            bonus_week_end_check = get_bonus_week_end()
                            if bonus_week_end_check.tzinfo is None:
                                bonus_week_end_check = bonus_week_end_check.replace(tzinfo=timezone.utc)
                            is_bonus_subscription_check = expires_at <= bonus_week_end_check if expires_at else False
                            bonus_week_still_active = is_bonus_week_active()
                            
                            # Если это подписка из бонусной недели и автопродление отключено, НЕ баним до окончания бонусной недели
                            # НО: Если бонусная неделя уже закончилась, баним даже если это была бонусная подписка
                            if is_bonus_subscription_check and not auto_renewal_enabled and not auto_payment_failed and bonus_week_still_active:
                                logger.info(f"ℹ️ Пользователь {telegram_id} имеет подписку из бонусной недели с отключенным автопродлением - не баним до окончания бонусной недели (бонусная неделя еще активна)")
                            else:
                                # Отзываем ссылку пользователя (делаем её невалидной)
                                from db import get_invite_link
                                user_invite_link = await get_invite_link(telegram_id)
                                if user_invite_link:
                                    await revoke_invite_link(user_invite_link)
                                    logger.info(f"✅ Ссылка пользователя {telegram_id} отозвана из-за истечения подписки")
                                
                                # Баним пользователя в канале (удаляем из канала) ТОЛЬКО если автопродление не удалось
                                # ВАЖНО: НЕ баним, если автопродление успешно
                                if not auto_payment_succeeded:
                                    try:
                                        await bot.ban_chat_member(
                                            chat_id=CHANNEL_ID,
                                            user_id=telegram_id,
                                            until_date=None  # Бан навсегда (пока не оплатит снова)
                                        )
                                        logger.info(f"✅ Пользователь {telegram_id} забанен в канале из-за истечения подписки")
                                    except Exception as ban_error:
                                        logger.warning(f"⚠️ Ошибка бана пользователя {telegram_id}: {ban_error}")
                                else:
                                    # Автопродление успешно - пользователь остается в канале
                                    logger.info(f"✅ Автопродление успешно для пользователя {telegram_id}, пользователь остается в канале")
                        
                        # ПРИМЕЧАНИЕ: Уведомления о неудачном автопродлении уже отправлены выше в блоке обработки неудачного платежа
                        # Этот блок оставляем для совместимости, но проверяем, не было ли уже отправлено уведомление
                        if auto_payment_failed:
                            # Проверяем, не было ли уже отправлено уведомление в блоке обработки неудачного платежа
                            # (там уже отправляются все необходимые сообщения с проверкой недостаточности средств)
                            notification_sent_key = f"auto_payment_failed_notification_{telegram_id}"
                            if notification_sent_key not in processed_users:
                                # Если уведомление еще не отправлено, отправляем (для случаев обработки исключений)
                                menu = await get_main_menu_for_user(telegram_id)
                                
                                await safe_send_message(
                                    bot=bot,
                                    chat_id=telegram_id,
                                    text="⚠️ <b>Автопродление отключено</b>\n\n"
                                        "Автоматическое продление доступа было отключено из-за неудачной попытки списания средств.\n\n"
                                        "💡 <b>Что делать:</b>\n"
                                        "• Используйте кнопку 💳 Получить доступ в меню для ручной оплаты\n\n"
                                        "Рекомендуем карты Тинькофф / Альфа / ВТБ для более надежной работы автопродления.",
                                    parse_mode="HTML",
                                    reply_markup=menu
                                )
                                processed_users[notification_sent_key] = datetime.now(timezone.utc)
                                logger.info(f"📧 Отправлено уведомление об отключении автопродления пользователю {telegram_id} (из блока обработки исключений)")
                        # Отправляем уведомление об истечении доступа ТОЛЬКО если:
                        # 1. Автопродление НЕ было успешным (auto_payment_succeeded = False)
                        # 2. Автопродление отключено или не работает, ИЛИ автоплатеж не удался
                        # ПРИМЕЧАНИЕ: Если auto_payment_failed = True, уведомление уже было отправлено выше
                        if not auto_payment_succeeded and not auto_payment_failed and (not auto_renewal_enabled or not saved_payment_method_id):
                            # Отправляем уведомление об истечении доступа (только один раз, больше никогда)
                            # Проверяем в БД, было ли уже отправлено уведомление
                            from db import get_subscription_expired_notified, set_subscription_expired_notified
                            
                            already_notified = await get_subscription_expired_notified(telegram_id)
                        
                            # Отправляем уведомление только если еще не отправляли
                            if not already_notified:
                                # ВАЖНО: Очищаем кэш и получаем актуальное меню (продакшн режим)
                                from db import _clear_cache
                                _clear_cache()
                                menu = await get_main_menu_for_user(telegram_id)
                                
                                await safe_send_message(
                                    bot=bot,
                                    chat_id=telegram_id,
                                    text="⏰ <b>Ваш доступ истек</b>\n\n"
                                        "Для продления доступа нажмите кнопку 💳 Получить доступ.",
                                    parse_mode="HTML",
                                    reply_markup=menu
                                )
                                # Помечаем в БД, что уведомление отправлено (навсегда)
                                await set_subscription_expired_notified(telegram_id, True)
                                logger.info(f"📧 Отправлено уведомление об истечении доступа пользователю {telegram_id} с обновленным меню (один раз, сохранено в БД)")
                                
                                # КРИТИЧНО: Добавляем пользователя в processed_users НАВСЕГДА после отправки уведомления
                                # Это предотвращает повторную обработку пользователя и возможные нежелательные действия
                                processed_users[telegram_id] = datetime.now(timezone.utc)
                                logger.info(f"✅ Пользователь {telegram_id} добавлен в processed_users после отправки уведомления об истечении (защита от повторной обработки)")
                            else:
                                logger.info(f"⏭️ Уведомление об истечении доступа уже было отправлено пользователю {telegram_id}, пропускаем")
                                # Даже если уведомление уже было отправлено, добавляем в processed_users чтобы не обрабатывать повторно
                                processed_users[telegram_id] = datetime.now(timezone.utc)
                        else:
                            # Автопродление успешно - не отправляем уведомление об истечении
                            logger.info(f"✅ Автопродление успешно для пользователя {telegram_id}, уведомление об истечении не отправляется")
                            # Добавляем пользователя в processed_users с текущим временем
                            processed_users[telegram_id] = datetime.now(timezone.utc)
                            logger.info(f"✅ Пользователь {telegram_id} добавлен в processed_users")
                        
                except Exception as e:
                    logger.error(f"❌ Ошибка обработки истекшей подписки для пользователя {telegram_id}: {e}")
            
            # Очищаем обработанных пользователей при достижении лимита (оставляем только последние N)
            if len(processed_users) > MAX_NOTIFIED_USERS_CACHE_SIZE:
                # Сортируем по времени и оставляем только последние N
                sorted_users = sorted(processed_users.items(), key=lambda x: x[1], reverse=True)
                processed_users = dict(sorted_users[:MAX_NOTIFIED_USERS_CACHE_SIZE])
                logger.info(f"🧹 Очищен processed_users, оставлено {len(processed_users)} записей")
                    
        except Exception as e:
            logger.error(f"❌ Ошибка в фоновой задаче проверки подписок: {e}")
            await asyncio.sleep(CHECK_EXPIRED_SUBSCRIPTIONS_INTERVAL_SECONDS)


# ================== YOOKASSA WEBHOOK ==================
@app.post("/yookassa/webhook")
async def yookassa_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    try:
        notification = WebhookNotificationFactory().create(data)
    except Exception as e:
        logger.error(f"❌ Ошибка создания notification: {e}")
        raise HTTPException(status_code=400, detail="Bad YooKassa notification")

    payment_obj = notification.object
    payment_id = payment_obj.id
    event = notification.event
    
    # Логируем все события для отладки
    logger.info(f"📥 Получено событие от ЮKassa: {event}, payment_id: {payment_id}")
    
    # ДЕТАЛЬНОЕ ЛОГИРОВАНИЕ ТИПА ПЛАТЕЖНОГО МЕТОДА (для диагностики SberPay и СБП)
    try:
        if hasattr(payment_obj, 'payment_method') and payment_obj.payment_method:
            pm_obj = payment_obj.payment_method
            pm_type = None
            pm_id = None
            if hasattr(pm_obj, 'type'):
                pm_type = pm_obj.type
            elif isinstance(pm_obj, dict) and 'type' in pm_obj:
                pm_type = pm_obj['type']
            if hasattr(pm_obj, 'id'):
                pm_id = pm_obj.id
            elif isinstance(pm_obj, dict) and 'id' in pm_obj:
                pm_id = pm_obj['id']
            logger.info(f"🔍 [WEBHOOK] Тип платежного метода: {pm_type}, payment_method_id: {pm_id}")
        else:
            logger.info(f"🔍 [WEBHOOK] payment_method отсутствует в notification.object")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка логирования типа платежного метода: {e}")

    # Обрабатываем отмененные/неудачные платежи
    if event == "payment.canceled":
        logger.info(f"🔄 Обработка canceled платежа: {payment_id}")
        try:
            # Убеждаемся, что timezone импортирован
            from datetime import timezone
            payment = Payment.find_one(payment_id)
            meta = payment.metadata or {}
            tg_user_id = meta.get("telegram_user_id")
            
            logger.info(f"📋 Метаданные платежа: {meta}, tg_user_id: {tg_user_id}")
            logger.debug(f"📋 Платеж из notification: {payment_obj}")
            
            if tg_user_id:
                tg_user_id = int(tg_user_id)
                
                # ПРОВЕРЯЕМ: когда был создан платеж (если старый - не отправляем сообщение)
                payment_created_at = None
                try:
                    if hasattr(payment, 'created_at'):
                        payment_created_at = payment.created_at
                    elif hasattr(payment_obj, 'created_at'):
                        payment_created_at = payment_obj.created_at
                    
                    # Проверяем в БД, когда был создан платеж
                    if not payment_created_at:
                        from db import get_active_pending_payment
                        payment_info = await get_active_pending_payment(tg_user_id, minutes=60)  # Ищем платежи за последний час
                        if payment_info and payment_info[0] == payment_id:
                            # Получаем created_at из БД
                            async with aiosqlite.connect(DB_PATH) as db_conn:
                                cursor = await db_conn.execute(
                                    "SELECT created_at FROM payments WHERE payment_id = ?",
                                    (payment_id,)
                                )
                                row = await cursor.fetchone()
                                if row:
                                    payment_created_at = row[0]
                    
                    # Если платеж старше 20 минут - не отправляем сообщение (это старый платеж)
                    if payment_created_at:
                        try:
                            if isinstance(payment_created_at, str):
                                created_at_dt = datetime.fromisoformat(payment_created_at.replace('Z', '+00:00'))
                            else:
                                created_at_dt = payment_created_at
                            
                            if created_at_dt.tzinfo is None:
                                created_at_dt = created_at_dt.replace(tzinfo=timezone.utc)
                            
                            now = datetime.now(timezone.utc)
                            time_since_creation = (now - created_at_dt).total_seconds() / 60
                            
                            if time_since_creation > 20:  # Платеж старше 20 минут
                                logger.info(f"ℹ️ Платеж {payment_id} отменен, но он был создан {time_since_creation:.1f} минут назад - это старый платеж, уведомление не отправляем")
                                await update_payment_status_async(payment_id, "canceled")
                                return {"ok": True, "event": "payment.canceled", "ignored": "old_payment"}
                        except Exception as e:
                            logger.warning(f"⚠️ Ошибка проверки времени создания платежа: {e}")
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка получения времени создания платежа: {e}")
                
                # КРИТИЧЕСКАЯ ПРОВЕРКА 1: Проверяем актуальный статус платежа из API YooKassa
                # Это самая надежная проверка - если платеж успешен в YooKassa, игнорируем canceled
                try:
                    current_payment_status = payment.status
                    if current_payment_status == "succeeded":
                        logger.info(f"✅ Платеж {payment_id} имеет статус 'succeeded' в API YooKassa - игнорируем событие canceled")
                        return {"ok": True, "event": "payment.canceled", "ignored": "payment_is_succeeded_in_api"}
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка проверки статуса платежа из API: {e}")
                
                # КРИТИЧЕСКАЯ ПРОВЕРКА 2: Проверяем, был ли платеж уже успешно обработан
                payment_already_succeeded = await already_processed(payment_id)
                if payment_already_succeeded:
                    logger.info(f"✅ Платеж {payment_id} уже был обработан (already_processed) - игнорируем событие canceled")
                    return {"ok": True, "event": "payment.canceled", "ignored": "already_processed"}
                
                # КРИТИЧЕСКАЯ ПРОВЕРКА 3: Проверяем статус платежа в БД
                async with aiosqlite.connect(DB_PATH) as db_check:
                    cursor = await db_check.execute(
                        "SELECT status FROM payments WHERE payment_id = ?",
                        (payment_id,)
                    )
                    row = await cursor.fetchone()
                    if row and row[0] == "succeeded":
                        logger.info(f"✅ Платеж {payment_id} уже имеет статус 'succeeded' в БД - игнорируем событие canceled")
                        return {"ok": True, "event": "payment.canceled", "ignored": "succeeded_in_db"}
                
                # КРИТИЧЕСКАЯ ПРОВЕРКА 4: Проверяем, есть ли у пользователя активная подписка (возможно, платеж уже обработан)
                from db import get_subscription_expires_at
                expires_at = await get_subscription_expires_at(tg_user_id)
                if expires_at:
                    now = datetime.now(timezone.utc)
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    if expires_at > now:
                        logger.info(f"✅ У пользователя {tg_user_id} уже есть активная подписка (до {expires_at}) - игнорируем событие canceled для платежа {payment_id}")
                        return {"ok": True, "event": "payment.canceled", "ignored": "user_has_active_subscription"}
                
                # КРИТИЧЕСКАЯ ПРОВЕРКА 5: Проверяем, не был ли создан платеж очень недавно (меньше 30 секунд)
                # Если платеж был создан меньше 30 секунд назад и пришел canceled, это может быть ошибка
                # или старый canceled, который пришел позже успешного платежа
                try:
                    if hasattr(payment, 'created_at'):
                        created_at = payment.created_at
                        if isinstance(created_at, str):
                            created_at_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        else:
                            created_at_dt = created_at
                        
                        if created_at_dt.tzinfo is None:
                            created_at_dt = created_at_dt.replace(tzinfo=timezone.utc)
                        
                        now_check = datetime.now(timezone.utc)
                        time_since_creation = (now_check - created_at_dt).total_seconds()
                        
                        # Если платеж был создан меньше 30 секунд назад, это подозрительно
                        # Возможно, это старый canceled, который пришел позже
                        if time_since_creation < 30:
                            logger.warning(f"⚠️ Платеж {payment_id} был создан всего {time_since_creation:.1f} секунд назад - подозрительно, проверяем статус еще раз")
                            # Проверяем статус еще раз из API
                            try:
                                refreshed_payment = Payment.find_one(payment_id)
                                if refreshed_payment.status == "succeeded":
                                    logger.info(f"✅ При повторной проверке платеж {payment_id} имеет статус 'succeeded' - игнорируем canceled")
                                    return {"ok": True, "event": "payment.canceled", "ignored": "succeeded_on_refresh"}
                            except Exception:
                                pass
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка проверки времени создания платежа для защиты: {e}")
                
                # Обновляем статус платежа в БД
                await update_payment_status_async(payment_id, "canceled")
                
                # Определяем причину отмены
                cancellation_reason = "отменен пользователем"  # По умолчанию считаем, что пользователь отменил
                message_text = ""
                
                # Проверяем причину отмены из разных источников
                try:
                    # Сначала проверяем payment_obj из notification (может содержать актуальные данные)
                    cancellation_details_notification = None
                    if hasattr(payment_obj, 'cancellation_details'):
                        cancellation_details_notification = payment_obj.cancellation_details
                    elif hasattr(payment_obj, 'cancellationDetails'):
                        cancellation_details_notification = payment_obj.cancellationDetails
                    
                    # Затем проверяем payment из API
                    cancellation_details = None
                    if hasattr(payment, 'cancellation_details'):
                        cancellation_details = payment.cancellation_details
                    elif hasattr(payment, 'cancellationDetails'):
                        cancellation_details = payment.cancellationDetails
                    
                    # Используем данные из notification, если есть, иначе из API
                    cancellation_details_final = cancellation_details_notification or cancellation_details
                    
                    reason = ""
                    party = ""
                    
                    # ДЕТАЛЬНОЕ ЛОГИРОВАНИЕ для диагностики SberPay
                    logger.info(f"🔍 [CANCELED] Детали отмены платежа {payment_id}:")
                    logger.info(f"   - cancellation_details_notification: {cancellation_details_notification}")
                    logger.info(f"   - cancellation_details (API): {cancellation_details}")
                    logger.info(f"   - cancellation_details_final: {cancellation_details_final}")
                    
                    if cancellation_details_final:
                        # Пробуем разные способы получения данных
                        if isinstance(cancellation_details_final, dict):
                            reason = str(cancellation_details_final.get('reason', '')).lower()
                            party = str(cancellation_details_final.get('party', '')).lower()
                        else:
                            reason = str(getattr(cancellation_details_final, 'reason', '')).lower()
                            party = str(getattr(cancellation_details_final, 'party', '')).lower()
                        
                        logger.info(f"🔍 Причина отмены: reason={reason}, party={party}")
                        logger.info(f"🔍 Полные детали отмены: {cancellation_details_final}")
                        
                        # Дополнительное логирование для SberPay
                        if hasattr(payment, 'payment_method') and payment.payment_method:
                            pm = payment.payment_method
                            pm_type = None
                            if hasattr(pm, 'type'):
                                pm_type = pm.type
                            elif isinstance(pm, dict) and 'type' in pm:
                                pm_type = pm['type']
                            logger.info(f"🔍 Тип платежного метода при отмене: {pm_type}")
                        
                        # Проверяем на недостаток средств (разные варианты) - ПРИОРИТЕТ 1
                        if any(keyword in reason for keyword in ['insufficient', 'funds', 'недостаточно', 'money', 'balance']):
                            cancellation_reason = "недостаточно средств"
                            message_text = (
                                "❌ Недостаточно средств на карте\n\n"
                                "💳 Проверьте баланс карты и попробуйте еще раз пройти по ссылке на оплату.\n\n"
                                "Для оплаты нажмите кнопку 💳 Получить доступ и перейдите по новой ссылке."
                            )
                        # Проверяем, отменил ли пользователь сам (выход из формы) - ПРИОРИТЕТ 2
                        elif 'user' in party:
                                cancellation_reason = "отменен пользователем (выход из формы)"
                                message_text = (
                                    "❌ Платёж был отменён\n\n"
                                    "Вы вышли из формы оплаты без завершения платежа.\n\n"
                                "Для оплаты нажмите кнопку 💳 Получить доступ и перейдите по новой ссылке."
                                )
                        elif any(keyword in reason for keyword in ['canceled_by_user', 'user_canceled']):
                            cancellation_reason = "отменен пользователем (выход из формы)"
                            message_text = (
                                "❌ Платёж был отменён\n\n"
                                "Вы вышли из формы оплаты без завершения платежа.\n\n"
                                "Для оплаты нажмите кнопку 💳 Получить доступ и перейдите по новой ссылке."
                            )
                        # Другие причины отмены (ошибки банка, сети и т.д.) - отправляем уведомление
                        else:
                            cancellation_reason = "отменен по другой причине"
                            message_text = (
                                "❌ Платёж был отменён\n\n"
                                "Оплата не была завершена.\n\n"
                                "Для оплаты нажмите кнопку 💳 Получить доступ и перейдите по новой ссылке."
                            )
                    else:
                        # Если нет деталей, по умолчанию отправляем уведомление
                        cancellation_reason = "отменен (причина неизвестна)"
                        message_text = (
                            "❌ Платёж был отменён\n\n"
                            "Оплата не была завершена.\n\n"
                            "Для оплаты нажмите кнопку 💳 Получить доступ и перейдите по новой ссылке."
                        )
                        
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка при определении причины отмены: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
                    # В случае ошибки отправляем стандартное уведомление
                    cancellation_reason = "отменен (ошибка определения причины)"
                    message_text = (
                        "❌ Платёж был отменён\n\n"
                        "Оплата не была завершена.\n\n"
                        "Для оплаты нажмите кнопку 💳 Получить доступ и перейдите по новой ссылке."
                    )
                
                # ФИНАЛЬНАЯ ПРОВЕРКА ПЕРЕД ОТПРАВКОЙ: еще раз проверяем статус из API
                # Это защита от race condition - если платеж стал succeeded между проверками
                try:
                    final_payment_check = Payment.find_one(payment_id)
                    if final_payment_check.status == "succeeded":
                        logger.info(f"✅ ФИНАЛЬНАЯ ПРОВЕРКА: Платеж {payment_id} имеет статус 'succeeded' - игнорируем canceled")
                        return {"ok": True, "event": "payment.canceled", "ignored": "succeeded_on_final_check"}
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка финальной проверки статуса платежа: {e}")
                
                # ПРОВЕРЯЕМ: есть ли у пользователя активная подписка
                has_active = await has_active_subscription(tg_user_id)
                
                # ПРОВЕРЯЕМ: является ли это автоплатежом (проверяем по метаданным или описанию)
                is_auto_payment = False
                payment_description = getattr(payment, 'description', '') or ''
                if 'автопродление' in payment_description.lower() or 'auto' in payment_description.lower():
                    is_auto_payment = True
                    logger.info(f"🔄 Обнаружен отмененный автоплатеж {payment_id} для пользователя {tg_user_id}")
                    
                    # Автоматически отключаем автопродление при отказе
                    from db import set_auto_renewal, is_auto_renewal_enabled, _clear_cache
                    auto_renewal_was_enabled = await is_auto_renewal_enabled(tg_user_id)
                    if auto_renewal_was_enabled:
                        await set_auto_renewal(tg_user_id, False)
                        _clear_cache()
                        logger.info(f"🔄 Автопродление автоматически отключено для пользователя {tg_user_id} из-за отказа автоплатежа")
                        
                        # Проверяем, была ли это недостаточность средств
                        insufficient_funds_detected = False
                        if cancellation_details_final:
                            reason_check = ""
                            if isinstance(cancellation_details_final, dict):
                                reason_check = str(cancellation_details_final.get('reason', '')).lower()
                            else:
                                reason_check = str(getattr(cancellation_details_final, 'reason', '')).lower()
                            
                            if any(keyword in reason_check for keyword in ['insufficient', 'funds', 'недостаточно', 'money', 'balance']):
                                insufficient_funds_detected = True
                        
                        # Отзываем ссылку пользователя
                        from db import get_invite_link
                        user_invite_link = await get_invite_link(tg_user_id)
                        if user_invite_link:
                            await revoke_invite_link(user_invite_link)
                            logger.info(f"✅ Ссылка пользователя {tg_user_id} отозвана из-за неудачного автопродления")
                        
                        # Баним пользователя в канале
                        try:
                            await bot.ban_chat_member(
                                chat_id=CHANNEL_ID,
                                user_id=tg_user_id,
                                until_date=None  # Бан навсегда
                            )
                            logger.info(f"✅ Пользователь {tg_user_id} забанен в канале из-за неудачного автопродления")
                        except Exception as ban_error:
                            logger.warning(f"⚠️ Ошибка бана пользователя {tg_user_id}: {ban_error}")
                        
                        # Обновляем меню и отправляем сообщения
                        menu = await get_main_menu_for_user(tg_user_id)
                        
                        # Отправляем сообщение о недостаточности средств (если это причина) или об общей ошибке
                        if insufficient_funds_detected:
                            await safe_send_message(
                                bot=bot,
                                chat_id=tg_user_id,
                                text=(
                                    "⚠️ <b>У вас недостаточно средств</b>\n\n"
                                    "На вашей карте недостаточно средств для автопродления подписки.\n"
                                    "Автопродление и доступ будут закрыты.\n\n"
                                    "Для возобновления доступа используйте кнопку 💳 Получить доступ."
                                ),
                                parse_mode="HTML",
                                reply_markup=menu
                            )
                            logger.warning(f"💰 Недостаточность средств для пользователя {tg_user_id}, payment_id: {payment_id}")
                        else:
                            await safe_send_message(
                                bot=bot,
                                chat_id=tg_user_id,
                                text=(
                                    "⚠️ <b>Автопродление не удалось</b>\n\n"
                                    "Не удалось списать средства с вашего способа оплаты.\n"
                                    "Автопродление автоматически отключено.\n\n"
                                    "Для продления доступа используйте кнопку 💳 Получить доступ."
                                ),
                                parse_mode="HTML",
                                reply_markup=menu
                            )
                        
                        # Отправляем уведомление об истечении доступа с обновленным меню
                        from db import get_subscription_expired_notified, set_subscription_expired_notified
                        already_notified_expired = await get_subscription_expired_notified(tg_user_id)
                        if not already_notified_expired:
                            await safe_send_message(
                                bot=bot,
                                chat_id=tg_user_id,
                                text="⏰ <b>Ваш доступ истек</b>\n\n"
                                    "Для продления доступа нажмите кнопку 💳 Получить доступ.",
                                parse_mode="HTML",
                                reply_markup=menu
                            )
                            await set_subscription_expired_notified(tg_user_id, True)
                            logger.info(f"📧 Отправлено уведомление об истечении доступа пользователю {tg_user_id} с обновленным меню")
                        
                        # Возвращаем успешный ответ, так как обработка завершена
                        return {"ok": True, "event": "payment.canceled", "auto_renewal_handled": True}
                
                if has_active:
                    # Если доступ активен - не отправляем уведомление об отмене старого платежа
                    logger.info(f"✅ Платеж {payment_id} отменен, но у пользователя {tg_user_id} уже есть активный доступ - уведомление не отправлено")
                    return {"ok": True, "event": "payment.canceled", "ignored": "user_has_active_subscription"}
                elif message_text:
                    # Уведомляем пользователя если есть текст сообщения
                    try:
                        await safe_send_message(bot=bot, chat_id=tg_user_id, text=message_text)
                        logger.info(f"✅ Отправлено уведомление об отмене платежа пользователю {tg_user_id}, причина: {cancellation_reason}")
                    except Exception as e:
                        logger.error(f"❌ Ошибка отправки уведомления об отмене платежа пользователю {tg_user_id}: {e}")
                else:
                    # Если message_text пустое или None - отправляем стандартное уведомление
                    try:
                        await safe_send_message(
                            bot=bot,
                            chat_id=tg_user_id,
                            text="❌ Платёж был отменён\n\n"
                                "Оплата не была завершена.\n\n"
                                "Для оплаты нажмите кнопку 💳 Получить доступ и перейдите по новой ссылке."
                        )
                        logger.info(f"✅ Отправлено стандартное уведомление об отмене платежа пользователю {tg_user_id}")
                    except Exception as e:
                        logger.error(f"❌ Ошибка отправки уведомления об отмене платежа пользователю {tg_user_id}: {e}")
            else:
                logger.warning(f"⚠️ Нет telegram_user_id в метаданных платежа {payment_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка обработки canceled платежа {payment_id}: {e}")
            import traceback
            traceback.print_exc()
        
        return {"ok": True, "event": "payment.canceled"}

    # Обрабатываем возвраты (refunds)
    if event == "refund.succeeded":
        logger.info(f"🔄 Обработка refund.succeeded: {payment_id}")
        try:
            # Получаем информацию о возврате
            refund_obj = notification.object
            payment_id_refund = refund_obj.payment_id if hasattr(refund_obj, 'payment_id') else None
            
            logger.info(f"📋 Информация о возврате: payment_id={payment_id_refund}")
            
            if payment_id_refund:
                # Получаем оригинальный платеж
                payment = Payment.find_one(payment_id_refund)
                meta = payment.metadata or {}
                tg_user_id = meta.get("telegram_user_id")
                
                logger.info(f"📋 Метаданные платежа: {meta}, tg_user_id: {tg_user_id}")
                
                if tg_user_id:
                    tg_user_id = int(tg_user_id)
                    
                    # Получаем сумму возврата
                    try:
                        if hasattr(refund_obj, 'amount'):
                            amount = refund_obj.amount.value if hasattr(refund_obj.amount, 'value') else str(refund_obj.amount.get('value', '0'))
                            currency = refund_obj.amount.currency if hasattr(refund_obj.amount, 'currency') else refund_obj.amount.get('currency', 'RUB')
                        else:
                            amount = "0"
                            currency = "RUB"
                    except Exception as e:
                        logger.warning(f"⚠️ Ошибка получения суммы возврата: {e}")
                        amount = "0"
                        currency = "RUB"
                    
                    # Отключаем подписку пользователя при возврате
                    try:
                        async with aiosqlite.connect(DB_PATH) as db_conn:
                            await db_conn.execute(
                                "DELETE FROM subscriptions WHERE telegram_id = ?",
                                (tg_user_id,)
                            )
                            await db_conn.execute(
                                "DELETE FROM approved_users WHERE telegram_user_id = ?",
                                (tg_user_id,)
                            )
                            await db_conn.commit()
                        logger.info(f"✅ Подписка пользователя {tg_user_id} отменена из-за возврата")
                    except Exception as e:
                        logger.warning(f"⚠️ Ошибка отмены подписки: {e}")
                    
                    # Уведомляем пользователя о возврате
                    try:
                        await safe_send_message(
                            bot=bot,
                            chat_id=tg_user_id,
                            text=f"💰 Возврат средств выполнен\n\n"
                            f"Сумма возврата: {amount} {currency}\n"
                            f"ID платежа: {payment_id_refund}\n\n"
                            f"Ваш доступ был отменен.\n"
                            f"Деньги будут возвращены на карту в течение нескольких рабочих дней."
                        )
                        logger.info(f"✅ Отправлено уведомление о возврате пользователю {tg_user_id}")
                    except Exception as e:
                        logger.error(f"❌ Ошибка отправки уведомления о возврате пользователю {tg_user_id}: {e}")
                else:
                    logger.warning(f"⚠️ Нет telegram_user_id в метаданных платежа {payment_id_refund}")
            else:
                logger.warning(f"⚠️ Не удалось получить payment_id из возврата")
        except Exception as e:
            logger.error(f"❌ Ошибка обработки refund.succeeded: {e}")
            import traceback
            traceback.print_exc()
        
        return {"ok": True, "event": "refund.succeeded"}

    # Обрабатываем успешные платежи
    if event != "payment.succeeded":
        return {"ok": True, "event": event}

    # КРИТИЧЕСКАЯ ПРОВЕРКА: Проверяем, был ли платеж уже обработан
    # Это предотвращает дублирование уведомлений и повторную активацию подписки
    if await already_processed(payment_id):
        logger.warning(f"⚠️ Платеж {payment_id} уже был обработан ранее - пропускаем повторную обработку")
        return {"ok": True, "duplicate": True}

    # Получаем актуальный статус платежа из API
    payment = Payment.find_one(payment_id)
    current_status = payment.status
    
    # ДЕТАЛЬНОЕ ЛОГИРОВАНИЕ ТИПА ПЛАТЕЖНОГО МЕТОДА ИЗ API
    try:
        if hasattr(payment, 'payment_method') and payment.payment_method:
            pm_api = payment.payment_method
            pm_type_api = None
            pm_id_api = None
            if hasattr(pm_api, 'type'):
                pm_type_api = pm_api.type
            elif isinstance(pm_api, dict) and 'type' in pm_api:
                pm_type_api = pm_api['type']
            if hasattr(pm_api, 'id'):
                pm_id_api = pm_api.id
            elif isinstance(pm_api, dict) and 'id' in pm_api:
                pm_id_api = pm_api['id']
            logger.info(f"🔍 [API] Тип платежного метода: {pm_type_api}, payment_method_id: {pm_id_api}")
            logger.info(f"🔍 [API] Полный payment_method объект: {pm_api}")
        else:
            logger.warning(f"⚠️ [API] payment_method отсутствует для платежа {payment_id}")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка логирования типа платежного метода из API: {e}")
    
    # КРИТИЧЕСКАЯ ПРОВЕРКА: проверяем статус ДО активации подписки
    if current_status != "succeeded":
        logger.warning(f"⚠️ Событие payment.succeeded получено, но статус платежа {payment_id} = {current_status}, игнорируем")
        await mark_processed(payment_id)
        return {"ok": True, "ignored": f"status is {current_status}, not succeeded"}

    # Дополнительная проверка: проверяем, что платеж действительно оплачен
    # Проверяем поле paid и captured
    try:
        # Проверяем поле paid (если доступно)
        if hasattr(payment, 'paid'):
            if not payment.paid:
                logger.warning(f"⚠️ Платеж {payment_id} не оплачен (paid=False), игнорируем")
                await mark_processed(payment_id)
                return {"ok": True, "ignored": "payment not paid"}
        
        # Проверяем поле captured (если доступно) - должно быть True для успешного платежа
        if hasattr(payment, 'captured'):
            if not payment.captured:
                logger.warning(f"⚠️ Платеж {payment_id} не захвачен (captured=False), игнорируем")
                await mark_processed(payment_id)
                return {"ok": True, "ignored": "payment not captured"}
        
        # Проверяем, что сумма платежа больше 0
        if hasattr(payment, 'amount'):
            amount_value = None
            if hasattr(payment.amount, 'value'):
                amount_value = float(payment.amount.value)
            elif isinstance(payment.amount, dict):
                amount_value = float(payment.amount.get('value', 0))
            
            if amount_value is not None and amount_value <= 0:
                logger.warning(f"⚠️ Платеж {payment_id} имеет нулевую или отрицательную сумму ({amount_value}), игнорируем")
                await mark_processed(payment_id)
                return {"ok": True, "ignored": f"invalid amount: {amount_value}"}
    except Exception as e:
        logger.error(f"❌ Ошибка проверки параметров платежа: {e}")
        import traceback
        logger.debug(traceback.format_exc())

    meta = payment.metadata or {}
    tg_user_id = meta.get("telegram_user_id")
    
    logger.info(f"🔍 Метаданные платежа {payment_id}: {meta}")
    logger.info(f"🔍 telegram_user_id из метаданных: {tg_user_id}")

    if not tg_user_id:
        logger.warning(f"⚠️ Нет telegram_user_id в метаданных платежа {payment_id}, пытаемся получить из БД")
        # Пытаемся получить правильный ID из БД
        async with aiosqlite.connect(DB_PATH) as db_conn:
            cursor = await db_conn.execute(
                "SELECT telegram_id FROM payments WHERE payment_id = ?",
                (payment_id,)
            )
            row = await cursor.fetchone()
            if row and row[0]:
                tg_user_id = str(row[0])
                logger.info(f"✅ Получен telegram_id из БД: {tg_user_id}")
            else:
                logger.error(f"❌ Не удалось найти telegram_id для платежа {payment_id}")
                await mark_processed(payment_id)
        return {"ok": True, "ignored": "no telegram_user_id"}

    tg_user_id = int(tg_user_id)

    # КРИТИЧЕСКАЯ ПРОВЕРКА: убеждаемся, что это не ID бота
    # Проверяем, что это разумный ID пользователя (обычно 9-10 цифр, начинается не с 0)
    tg_user_id_str = str(tg_user_id)
    if tg_user_id_str.startswith('0') or len(tg_user_id_str) < 6:
        logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Подозрительный telegram_user_id: {tg_user_id} для платежа {payment_id}")
        # Пытаемся получить правильный ID из БД
        async with aiosqlite.connect(DB_PATH) as db_conn:
            cursor = await db_conn.execute(
                "SELECT telegram_id FROM payments WHERE payment_id = ?",
                (payment_id,)
            )
            row = await cursor.fetchone()
            if row and row[0]:
                tg_user_id = int(row[0])
                logger.info(f"✅ Используем telegram_id из БД: {tg_user_id}")
            else:
                logger.error(f"❌ Не удалось найти правильный telegram_id для платежа {payment_id}")
                await mark_processed(payment_id)
                return {"ok": True, "ignored": "invalid telegram_user_id"}
    
    logger.info(f"✅ Финальный telegram_user_id для обработки: {tg_user_id}")

    # Еще раз проверяем статус перед активацией подписки (на случай если изменился)
    payment_refresh = Payment.find_one(payment_id)
    if payment_refresh.status != "succeeded":
        logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Статус платежа {payment_id} изменился с succeeded на {payment_refresh.status} перед активацией подписки!")
        await mark_processed(payment_id)
        return {"ok": True, "ignored": f"status changed to {payment_refresh.status}"}
    
    # Финальная проверка: убеждаемся что платеж действительно успешен
    if payment_refresh.status != "succeeded":
        logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Финальная проверка - статус платежа {payment_id} = {payment_refresh.status}, не succeeded!")
        await mark_processed(payment_id)
        return {"ok": True, "ignored": f"final check failed: {payment_refresh.status}"}

    # ВАЖНО: Активируем подписку для ВСЕХ типов платежей (SberPay, СБП, карта)
    # независимо от типа payment_method
    
    # Сначала определяем тип платежного метода для логирования
    payment_method_type = None
    payment_method_id = None
    payment_method_saved = False
    
    # Проверяем наличие payment_method и его статус сохранения
    logger.info(f"🔍 Проверка payment_method для платежа {payment_id}, пользователь {tg_user_id}")
    if hasattr(payment, 'payment_method') and payment.payment_method:
        pm = payment.payment_method
        logger.info(f"📋 payment_method найден: {type(pm)}")
        
        # Определяем тип платежного метода
        if hasattr(pm, 'type'):
            payment_method_type = pm.type
        elif isinstance(pm, dict) and 'type' in pm:
            payment_method_type = pm['type']
        logger.info(f"🔍 Тип платежного метода: {payment_method_type}")
        
        # Проверяем, сохранен ли метод оплаты
        if hasattr(pm, 'saved'):
            payment_method_saved = bool(pm.saved)
            logger.info(f"💾 payment_method.saved = {payment_method_saved} (атрибут)")
        elif isinstance(pm, dict):
            payment_method_saved = bool(pm.get('saved', False))
            logger.info(f"💾 payment_method['saved'] = {payment_method_saved} (dict)")
        else:
            logger.warning(f"⚠️ Не удалось определить saved для payment_method: {pm}")
        
        # Получаем ID метода оплаты
        # ВАЖНО: Для автоплатежей в YooKassa payment_method.id может быть равен payment.id
        # Это нормально - YooKassa использует этот ID для последующих автоплатежей
        # Но нужно убедиться, что способ оплаты действительно сохранен (saved=True)
        if hasattr(pm, 'id'):
            payment_method_id = pm.id
            logger.info(f"🆔 payment_method.id = {payment_method_id} (атрибут)")
            # Проверяем, не равен ли он payment.id
            if payment_method_id == payment_id:
                logger.info(f"ℹ️ payment_method.id ({payment_method_id}) равен payment.id - это нормально для первого платежа в YooKassa")
            # Пробуем получить дополнительную информацию о карте
            if hasattr(pm, 'card'):
                card_info = {}
                if hasattr(pm.card, 'last4'):
                    card_info['last4'] = pm.card.last4
                if hasattr(pm.card, 'card_type'):
                    card_info['card_type'] = pm.card.card_type
                logger.info(f"💳 Информация о карте: {card_info}")
        elif isinstance(pm, dict) and 'id' in pm:
            payment_method_id = pm['id']
            logger.info(f"🆔 payment_method['id'] = {payment_method_id} (dict)")
            if payment_method_id == payment_id:
                logger.info(f"ℹ️ payment_method['id'] ({payment_method_id}) равен payment.id - это нормально для первого платежа в YooKassa")
        else:
            logger.warning(f"⚠️ Не удалось получить id для payment_method: {pm}")
    else:
        logger.warning(f"⚠️ payment_method отсутствует или None для платежа {payment_id} - это нормально для SberPay и СБП")
    
    # ВАЖНО: Активируем подписку для ВСЕХ типов платежей (SberPay, СБП, банковская карта)
    # независимо от наличия или типа payment_method
    # разрешаем пользователю вступление
    await allow_user(tg_user_id)
    
    # ЛОГИРУЕМ тип платежа для диагностики
    payment_type_name = "неизвестен"
    if payment_method_type:
        pm_type_lower = payment_method_type.lower()
        if pm_type_lower == 'sbp':
            payment_type_name = "СБП"
        elif pm_type_lower in ['sberbank', 'sberpay']:
            payment_type_name = "SberPay"
        elif pm_type_lower in ['bank_card', 'card']:
            payment_type_name = "Банковская карта"
        else:
            payment_type_name = payment_method_type
    else:
        payment_type_name = "без payment_method (возможно СБП или SberPay)"
    
    logger.info(f"💳 Обработка платежа типа: {payment_type_name} для пользователя {tg_user_id}")
    
    # Активируем подписку СРАЗУ после успешного платежа (для ВСЕХ типов: SberPay, СБП, карта)
    # Определяем длительность в зависимости от режима (бонусная неделя или продакшн)
    remaining_time = None
    bonus_end = None
    if is_bonus_week_active():
        # Бонусная неделя: используем ОСТАВШЕЕСЯ время до конца бонусной недели
        # ВАЖНО: expires_at должен быть равен bonus_week_end, а не starts_at + dni_prazdnika
        from datetime import timezone as tz
        from config import get_bonus_week_end
        now = datetime.now(tz.utc)
        bonus_end = get_bonus_week_end()
        # Убеждаемся, что bonus_end имеет timezone
        if bonus_end.tzinfo is None:
            bonus_end = bonus_end.replace(tzinfo=tz.utc)
        remaining_time = bonus_end - now
        if remaining_time.total_seconds() <= 0:
            # Бонусная неделя уже закончилась - используем продакшн
            subscription_duration = SUBSCRIPTION_DAYS
            logger.info(f"⚠️ Бонусная неделя уже закончилась для пользователя {tg_user_id}, используем продакшн длительность: {subscription_duration} дней")
        else:
            # Конвертируем секунды в дни
            subscription_duration = remaining_time.total_seconds() / 86400
            logger.info(f"🎁 Бонусная неделя активна для пользователя {tg_user_id}, оставшееся время: {remaining_time.total_seconds() / 60:.1f} минут ({subscription_duration:.6f} дней), bonus_end={bonus_end.isoformat()}, now={now.isoformat()}")
    else:
        # Продакшн режим: используем обычную длительность
        subscription_duration = SUBSCRIPTION_DAYS
        logger.info(f"💼 Продакшн режим для пользователя {tg_user_id}, длительность: {subscription_duration} дней")
    
    # ВАЖНО: Для бонусной недели устанавливаем expires_at = bonus_week_end напрямую
    # КРИТИЧЕСКИ ВАЖНО: starts_at должен быть моментом оплаты (now), а не началом бонусной недели
    # expires_at всегда фиксированное время окончания бонусной недели (начало + 15 минут)
    if is_bonus_week_active() and remaining_time and remaining_time.total_seconds() > 0:
        # Устанавливаем expires_at = bonus_week_end напрямую, чтобы не было проблем с округлением
        # ВАЖНО: Используем tz.utc, так как мы импортировали timezone как tz выше
        # КРИТИЧЕСКИ ВАЖНО: starts_at = момент оплаты (now), expires_at = фиксированное время окончания бонусной недели
        starts_at = now  # Момент оплаты (когда пользователь зарегистрировался)
        expires_at = bonus_end  # Фиксированное время окончания бонусной недели (начало + 15 минут)
        logger.info(f"🎁 Установка подписки для бонусной недели: starts_at={starts_at.isoformat()} (момент оплаты), expires_at={expires_at.isoformat()} (фиксированное окончание), bonus_week_end={bonus_end.isoformat()}")
        
        async with aiosqlite.connect(DB_PATH) as db_conn:
            # гарантируем, что юзер существует
            await db_conn.execute(
                "INSERT OR IGNORE INTO users (telegram_id, username, created_at) VALUES (?, ?, ?)",
                (tg_user_id, None, datetime.now(tz.utc).isoformat())
            )
            
            # upsert подписки (сохраняем дату начала и окончания)
            await db_conn.execute(
                """
                INSERT INTO subscriptions (telegram_id, expires_at, starts_at, subscription_expired_notified)
                VALUES (?, ?, ?, 0) ON CONFLICT(telegram_id) DO
                UPDATE SET expires_at=excluded.expires_at, starts_at=excluded.starts_at,
                           subscription_expired_notified=0
                """,
                (tg_user_id, expires_at.isoformat(), starts_at.isoformat())
            )
            await db_conn.commit()
            logger.info(f"💾 Подписка сохранена в БД (бонусная неделя): telegram_id={tg_user_id}, expires_at={expires_at.isoformat()}, starts_at={starts_at.isoformat()}")
    else:
        # Для продакшн режима используем обычную активацию
        await activate_subscription(tg_user_id, days=subscription_duration)
    logger.info(f"✅ Подписка активирована для пользователя {tg_user_id} на {format_subscription_duration(subscription_duration)} (тип платежа: {payment_type_name})")
    
    # КРИТИЧЕСКИ ВАЖНО: Очищаем кэш подписки сразу после активации
    from db import _clear_cache
    _clear_cache()
    
    # Даем небольшую задержку для гарантии, что БД обновилась
    await asyncio.sleep(0.3)
    
    # ПРОВЕРЯЕМ, что подписка действительно сохранена в БД
    async with aiosqlite.connect(DB_PATH) as db_verify:
        cursor_verify = await db_verify.execute(
            "SELECT expires_at FROM subscriptions WHERE telegram_id = ?",
            (tg_user_id,)
        )
        row_verify = await cursor_verify.fetchone()
        if row_verify and row_verify[0]:
            from datetime import timezone
            expires_at_verify = datetime.fromisoformat(row_verify[0])
            if expires_at_verify.tzinfo is None:
                expires_at_verify = expires_at_verify.replace(tzinfo=timezone.utc)
            now_verify = datetime.now(timezone.utc)
            is_active_verify = expires_at_verify > now_verify
            logger.info(f"✅ ПОДТВЕРЖДЕНО: Подписка сохранена в БД для пользователя {tg_user_id}, expires_at={expires_at_verify.isoformat()}, is_active={is_active_verify}")
        else:
            logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Подписка НЕ найдена в БД для пользователя {tg_user_id} после активации!")
    
    _clear_cache()
    
    # Проверяем, что подписка действительно активна после активации
    has_active_after = await has_active_subscription(tg_user_id)
    logger.info(f"🔍 Проверка после активации: has_active_subscription({tg_user_id}) = {has_active_after}")
    
    # Сохраняем payment_method_id и автоматически включаем автопродление
    # КРИТИЧЕСКИ ВАЖНО: Для СБП и SberPay payment_method_saved может быть False,
    # но payment_method_id может быть доступен - в этом случае тоже включаем автопродление
    # ВАЖНО: Автопродление включаем если:
    # 1. payment_method_id есть
    # 2. payment_method_saved = True ИЛИ тип платежа поддерживает автоплатежи (для СБП и SberPay)
    # 3. Тип платежного метода поддерживает автоплатежи (bank_card, card, sbp, sberbank, sberpay)
    # 
    # ПРИМЕЧАНИЕ: Для СБП и SberPay автоплатежи могут не работать на стороне ЮKassa,
    # но мы попробуем - если не сработает, увидим ошибку в логах при попытке автопродления
    supported_types = ['bank_card', 'card', 'sbp', 'sberbank', 'sberpay']
    should_enable_auto_renewal = False
    
    if payment_method_id:
        # Проверяем, поддерживается ли тип платежного метода
        if payment_method_type and payment_method_type.lower() in supported_types:
            # Для поддерживаемых типов ВСЕГДА включаем автопродление (данные всегда сохраняются при оплате по договору с ЮKassa)
            from db import save_payment_method, set_auto_renewal
            await save_payment_method(tg_user_id, payment_method_id)
            logger.info(f"💾 Сохранен payment_method_id для пользователя {tg_user_id}: {payment_method_id} (тип: {payment_method_type})")
            
            # Включаем автопродление
            await set_auto_renewal(tg_user_id, True)
            logger.info(f"✅ Автопродление автоматически включено для пользователя {tg_user_id} (тип: {payment_method_type}, данные всегда сохраняются при оплате)")
            
            # КРИТИЧЕСКИ ВАЖНО: Очищаем кэш ПОСЛЕ установки автопродления, чтобы обработчик "Управление доступом"
            # сразу видел актуальное значение автопродления
            _clear_cache()
            
            # Уведомляем пользователя о сохранении способа оплаты и включении автопродления
            payment_method_name = "карта"  # По умолчанию
            if payment_method_type:
                pm_type_lower = payment_method_type.lower()
                if pm_type_lower == 'sbp':
                    payment_method_name = "СБП"
                elif pm_type_lower in ['sberbank', 'sberpay']:
                    payment_method_name = "SberPay"
            
            # Определяем текст в зависимости от режима (бонусная неделя или продакшн)
            if is_bonus_week_active():
                auto_renewal_text = (
                    f"🔄 <b>Автопродление включено</b>\n\n"
                    f"⚠️ <b>После окончания бонусной недели:</b>\n"
                    f"• Будет автоматически списана полная стоимость: <b>2990 рублей на 30 дней</b>\n"
                    f"• Доступ будет автоматически продлеваться каждые <b>30 дней</b>\n"
                    f"• Автопродление можно отключить в меню «Управление доступом» до окончания бонусной недели\n\n"
                )
            else:
                auto_renewal_text = (
                    f"🔄 Доступ будет автоматически продлеваться каждые {format_subscription_duration(SUBSCRIPTION_DAYS)}.\n\n"
                )
            
            # Отправляем уведомление о сохранении способа оплаты только один раз
            # Используем payment_id как уникальный ключ для предотвращения дублирования
            # ВАЖНО: Проверяем, что автопродление все еще включено перед отправкой уведомления
            from db import is_auto_renewal_enabled
            auto_renewal_still_enabled = await is_auto_renewal_enabled(tg_user_id)
            
            # КРИТИЧЕСКАЯ ПРОВЕРКА: Используем комбинацию payment_id и tg_user_id для уникальности
            # Это предотвращает дублирование даже если webhook обрабатывается дважды
            notification_key = f"pm_saved_{payment_id}_{tg_user_id}"
            
            # ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА: Проверяем, не отправляли ли мы уже уведомление для этого платежа
            # Используем также простой ключ по payment_id для дополнительной защиты
            notification_key_simple = f"pm_saved_{payment_id}"
            
            # Проверяем оба ключа
            already_sent = await already_processed(notification_key) or await already_processed(notification_key_simple)
            
            if not already_sent and auto_renewal_still_enabled:
                await safe_send_message(
                    bot=bot,
                    chat_id=tg_user_id,
                    text=f"💳 <b>{payment_method_name.capitalize()} сохранена для автопродления</b>\n\n"
                        f"✅ Ваш способ оплаты сохранен и будет использоваться для автоматического продления доступа.\n\n"
                        f"{auto_renewal_text}"
                        "⚠️ <b>Важно:</b> Автопродление может не работать для некоторых способов оплаты.\n"
                        "Если автопродление не сработает, вы получите уведомление и сможете продлить доступ вручную.\n\n"
                        "⚙️ Вы можете отключить автопродление в меню «Управление доступом».",
                    parse_mode="HTML"
                )
                # Помечаем, что уведомление отправлено (ОБА ключа для надежности)
                await mark_processed(notification_key)
                await mark_processed(notification_key_simple)
                logger.info(f"✅ Отправлено уведомление о сохранении способа оплаты для пользователя {tg_user_id}, payment_id: {payment_id}")
            elif already_sent:
                logger.info(f"⏭️ Уведомление о сохранении способа оплаты уже было отправлено для платежа {payment_id}, пропускаем")
            elif not auto_renewal_still_enabled:
                logger.info(f"⏭️ Автопродление отключено пользователем {tg_user_id} - не отправляем уведомление о сохранении способа оплаты")
    else:
        if not payment_method_id:
            logger.info(f"ℹ️ Платеж {payment_id}: payment_method_id отсутствует - автопродление НЕ будет включено")
        elif payment_method_type and payment_method_type.lower() not in supported_types:
            logger.info(f"ℹ️ Платеж {payment_id}: тип платежного метода {payment_method_type} не поддерживает автопродление (поддерживаются: {', '.join(supported_types)})")
    
    # Обновляем статус платежа в БД
    await update_payment_status_async(payment_id, "succeeded")

    # Сначала проверяем и разбаниваем пользователя, если он был забанен
    try:
        await bot.unban_chat_member(
            chat_id=CHANNEL_ID,
            user_id=tg_user_id,
            only_if_banned=True  # Разбаниваем только если был забанен
        )
    except Exception:
        pass  # Игнорируем ошибки разбана

    # Получаем дату окончания подписки для установки expire_date ссылки
    from db import get_subscription_expires_at
    subscription_expires_at = await get_subscription_expires_at(tg_user_id)

    # Создаем ПРИГЛАСИТЕЛЬНУЮ ссылку (прямой доступ) - пользователь заплатил!
    # Ссылка будет одноразовой (member_limit=1) и действительна до окончания подписки
    invite_link = None
    try:
        # Используем expires_at подписки как expire_date ссылки
        # Если подписка истекает через N дней, ссылка будет валидна N дней
        if subscription_expires_at:
            link_expire_date = subscription_expires_at
        else:
            if is_bonus_week_active():
                link_expire_date = datetime.now(timezone.utc) + timedelta(days=subscription_duration)
            else:
                link_expire_date = datetime.now(timezone.utc) + timedelta(days=SUBSCRIPTION_DAYS)
        
        # КРИТИЧЕСКИ ВАЖНО: Ссылка должна быть УНИКАЛЬНОЙ для каждого пользователя
        # Срок действия ссылки = срок доступа пользователя (expires_at подписки)
        # Другой пользователь НЕ может использовать чужую ссылку
        # 
        # Создаем ссылку С заявкой на вступление для проверки владельца
        # ВАЖНО: member_limit нельзя использовать с creates_join_request=True
        # Защита от использования другими будет через проверку в обработчике заявок
        invite_link = await safe_create_invite_link(
            bot=bot,
                chat_id=CHANNEL_ID,
            creates_join_request=True,  # С заявкой - для проверки владельца
            expire_date=link_expire_date  # Ссылка действительна до окончания подписки пользователя
        )
        
        if not invite_link:
            # Если не получилось с заявкой, пробуем без заявки с member_limit=1 (одноразовая ссылка)
            logger.warning(f"⚠️ Первая попытка создания ссылки не удалась, пробуем второй вариант (одноразовая ссылка)")
            invite_link = await safe_create_invite_link(
                bot=bot,
                chat_id=CHANNEL_ID,
                creates_join_request=False,
                member_limit=1,  # Одноразовая ссылка - только один пользователь может использовать
                expire_date=link_expire_date  # Ссылка действительна до окончания подписки пользователя
            )
        
        if not invite_link:
            # Если и это не получилось, пробуем еще раз с заявкой (последняя попытка)
            logger.warning(f"⚠️ Вторая попытка не удалась, пробуем еще раз с заявкой")
            try:
                chat_invite = await bot.create_chat_invite_link(
                    chat_id=CHANNEL_ID,
                    creates_join_request=True,
                    expire_date=link_expire_date
                )
                invite_link = chat_invite.invite_link
                logger.info(f"✅ Создана ссылка с заявкой (последняя попытка) для пользователя {tg_user_id}")
            except Exception as final_error:
                logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось создать ссылку с заявкой: {final_error}")
                # Пробуем еще раз с member_limit=1
                try:
                    chat_invite = await bot.create_chat_invite_link(
                        chat_id=CHANNEL_ID,
                        creates_join_request=False,
                        member_limit=1,
                        expire_date=link_expire_date
                    )
                    invite_link = chat_invite.invite_link
                    logger.info(f"✅ Создана одноразовая ссылка (последняя попытка) для пользователя {tg_user_id}")
                except Exception as final_fallback_error:
                    logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Все попытки создания уникальной ссылки не удались: {final_fallback_error}")
                    raise Exception(f"Не удалось создать уникальную ссылку на канал для пользователя {tg_user_id} после всех попыток")
        
        # КРИТИЧЕСКИ ВАЖНО: Ссылка должна быть создана ВСЕГДА и быть УНИКАЛЬНОЙ для пользователя
        if not invite_link:
            logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Уникальная ссылка на канал не была создана для пользователя {tg_user_id} после всех попыток!")
            raise Exception(f"Не удалось создать уникальную ссылку на канал для пользователя {tg_user_id}")
        
        logger.info(f"✅ Создана УНИКАЛЬНАЯ индивидуальная ссылка для пользователя {tg_user_id}, действительна до {link_expire_date} (срок доступа пользователя)")
    except Exception as e:
        logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА создания пригласительной ссылки: {e}")
        import traceback
        traceback.print_exc()
        # КРИТИЧЕСКИ ВАЖНО: Пробуем еще раз создать УНИКАЛЬНУЮ ссылку перед возвратом ошибки
        try:
            logger.warning(f"⚠️ Последняя попытка создания уникальной ссылки для пользователя {tg_user_id}")
            # Пробуем с заявкой
            try:
                chat_invite = await bot.create_chat_invite_link(
                    chat_id=CHANNEL_ID,
                    creates_join_request=True,
                    expire_date=link_expire_date
                )
                invite_link = chat_invite.invite_link
                logger.info(f"✅ Уникальная ссылка создана в последней попытке (с заявкой) для пользователя {tg_user_id}")
            except Exception:
                # Если не получилось с заявкой, пробуем с member_limit=1
                chat_invite = await bot.create_chat_invite_link(
                    chat_id=CHANNEL_ID,
                    creates_join_request=False,
                    member_limit=1,  # Одноразовая ссылка - уникальна для пользователя
                    expire_date=link_expire_date
                )
                invite_link = chat_invite.invite_link
                logger.info(f"✅ Уникальная ссылка создана в последней попытке (одноразовая) для пользователя {tg_user_id}")
        except Exception as final_error:
            logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось создать уникальную ссылку даже в последней попытке: {final_error}")
            # Отправляем сообщение об ошибке, но НЕ прерываем обработку платежа
            menu = await get_main_menu_for_user(tg_user_id)
            await safe_send_message(
                bot=bot,
                chat_id=tg_user_id,
                text="✅ <b>Оплата подтверждена!</b>\n\n"
                "⚠️ Произошла ошибка при создании уникальной ссылки. Пожалуйста, свяжитесь с администратором для получения доступа.",
                parse_mode="HTML",
            reply_markup=menu
        )
            # НЕ возвращаем ошибку - продолжаем обработку платежа
            invite_link = None  # Устанавливаем в None, чтобы дальше обработать это

    # Получаем даты начала и окончания подписки (уже сохранены выше)
    from db import get_subscription_expires_at, get_subscription_starts_at
    expires_at_dt = await get_subscription_expires_at(tg_user_id)
    starts_at_dt = await get_subscription_starts_at(tg_user_id)
    
    # Сохраняем информацию о ссылке в БД и отправляем сообщение
    # КРИТИЧЕСКИ ВАЖНО: Уведомление об оплате должно отправляться ВСЕГДА для ВСЕХ типов платежей (СБП, SberPay, карта)
    # даже если ссылка не создалась - пользователь заплатил и должен получить уведомление
    if invite_link:
        await save_invite_link(invite_link, tg_user_id, payment_id)
        
        # Форматируем даты для отображения
        # КРИТИЧЕСКИ ВАЖНО: Для бонусной недели expires_at всегда должен быть фиксированным временем окончания бонусной недели
        if starts_at_dt and expires_at_dt:
            # Если это бонусная неделя, убеждаемся что expires_at = фиксированное время окончания
            if is_bonus_week_active():
                from config import get_bonus_week_end
                bonus_week_end_fixed = get_bonus_week_end()
                if bonus_week_end_fixed.tzinfo is None:
                    bonus_week_end_fixed = bonus_week_end_fixed.replace(tzinfo=timezone.utc)
                # Используем фиксированное время окончания бонусной недели
                expires_at_dt = bonus_week_end_fixed
            starts_str = format_datetime_moscow(starts_at_dt)
            expires_str = format_datetime_moscow(expires_at_dt)
        else:
            # Если даты не найдены, используем текущее время (запасной вариант)
            starts_at_dt = datetime.now(timezone.utc)
            if is_bonus_week_active():
                from config import get_bonus_week_end
                expires_at_dt = get_bonus_week_end()
                if expires_at_dt.tzinfo is None:
                    expires_at_dt = expires_at_dt.replace(tzinfo=timezone.utc)
            else:
                expires_at_dt = starts_at_dt + timedelta(days=SUBSCRIPTION_DAYS)
            starts_str = format_datetime_moscow(starts_at_dt)
            expires_str = format_datetime_moscow(expires_at_dt)

        # КРИТИЧЕСКИ ВАЖНО: ПРИНУДИТЕЛЬНО создаем правильное меню после успешной оплаты
        # НЕ полагаемся на get_main_menu_for_user - создаем меню напрямую
        # ВАЖНО: Это работает для ВСЕХ типов платежей (SberPay, СБП, банковская карта) одинаково
        from db import _clear_cache
        from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
        
        _clear_cache()
        await asyncio.sleep(0.3)  # Увеличиваем задержку для гарантии обновления БД
        
        # ПРОВЕРЯЕМ напрямую в БД, что подписка сохранена (для ВСЕХ типов платежей)
        async with aiosqlite.connect(DB_PATH) as db_check:
            cursor = await db_check.execute(
                "SELECT expires_at FROM subscriptions WHERE telegram_id = ?",
                (tg_user_id,)
            )
            row = await cursor.fetchone()
            if row and row[0]:
                from datetime import timezone
                expires_at_check = datetime.fromisoformat(row[0])
                if expires_at_check.tzinfo is None:
                    expires_at_check = expires_at_check.replace(tzinfo=timezone.utc)
                now_check = datetime.now(timezone.utc)
                is_active_db = expires_at_check > now_check
                logger.info(f"✅ ПОДТВЕРЖДЕНО: Подписка в БД для пользователя {tg_user_id} (тип платежа: {payment_type_name}), expires_at={expires_at_check.isoformat()}, is_active={is_active_db}")
            else:
                logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Подписка НЕ найдена в БД для пользователя {tg_user_id} (тип платежа: {payment_type_name})!")
        
        # ПРИНУДИТЕЛЬНО создаем правильное меню в зависимости от режима
        # ВАЖНО: Это работает для ВСЕХ типов платежей одинаково (СБП, SberPay, банковская карта)
        # ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА: Проверяем, не закончилась ли бонусная неделя по времени окончания
        bonus_week_active_check = is_bonus_week_active()
        from config import get_bonus_week_end
        bonus_week_end_check = get_bonus_week_end()
        if bonus_week_end_check.tzinfo is None:
            bonus_week_end_check = bonus_week_end_check.replace(tzinfo=timezone.utc)
        now_check_menu = datetime.now(timezone.utc)
        # Если текущее время больше времени окончания бонусной недели - бонусная неделя закончилась
        if now_check_menu > bonus_week_end_check:
            bonus_week_active_check = False  # Принудительно устанавливаем, что бонусная неделя закончилась
        
        if bonus_week_active_check:
            # БОНУСНАЯ НЕДЕЛЯ: После успешной оплаты ВСЕГДА показываем "Управление доступом"
            BTN_MANAGE_SUB = "⚙️ Управление доступом"
            BTN_ABOUT_1 = "ℹ️ О проекте"
            menu = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text=BTN_MANAGE_SUB)],
                    [KeyboardButton(text=BTN_ABOUT_1)],
                ],
                resize_keyboard=True,
            )
            logger.info(f"✅ ПРИНУДИТЕЛЬНО создано меню БОНУСНОЙ НЕДЕЛИ с 'Управление доступом' для пользователя {tg_user_id} (тип платежа: {payment_type_name})")
        else:
            # ПРОДАКШН: Используем стандартное меню
            BTN_MANAGE_SUB = "⚙️ Управление доступом"
            BTN_STATUS_1 = "📊 Статус доступа"
            BTN_ABOUT_1 = "ℹ️ О проекте"
            BTN_CHECK_1 = "🔍 Проверить оплату"
            BTN_SUPPORT = "💬 Поддержка"
            menu = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text=BTN_MANAGE_SUB)],
                    [KeyboardButton(text=BTN_STATUS_1)],
                    [KeyboardButton(text=BTN_ABOUT_1)],
                    [KeyboardButton(text=BTN_CHECK_1)],
                    [KeyboardButton(text=BTN_SUPPORT)],
                ],
                resize_keyboard=True,
            )
            logger.info(f"✅ Создано меню ПРОДАКШН с 'Управление доступом' для пользователя {tg_user_id} (тип платежа: {payment_type_name})")
        
        menu_buttons = [btn.text for row in menu.keyboard for btn in row] if hasattr(menu, 'keyboard') else 'N/A'
        logger.info(f"🔍 ФИНАЛЬНОЕ меню для пользователя {tg_user_id} (тип платежа: {payment_type_name}): {menu_buttons}")
        
        # Форматируем длительность доступа для отображения (используем subscription_duration из активации)
        # КРИТИЧЕСКИ ВАЖНО: Для бонусной недели вычисляем длительность в минутах
        # Используем ту же проверку, что и для меню (bonus_week_active_check)
        if bonus_week_active_check and starts_at_dt and expires_at_dt:
            # Вычисляем разницу в минутах для бонусной недели
            time_diff = expires_at_dt - starts_at_dt
            minutes_diff = int(time_diff.total_seconds() / 60)
            if minutes_diff == 1:
                duration_text = "1 минута"
            elif 2 <= minutes_diff <= 4:
                duration_text = f"{minutes_diff} минуты"
            else:
                duration_text = f"{minutes_diff} минут"
        else:
            # Для продакшн режима используем обычное форматирование
            duration_text = format_subscription_duration(subscription_duration)
        
        # Формируем текст в зависимости от режима (бонусная неделя или продакшн)
        if is_bonus_week_active():
            bonus_warning = (
                "\n\n🎉 <b>БОНУСНАЯ НЕДЕЛЯ</b>\n"
                f"⏰ Ваш доступ действует до окончания бонусной недели\n\n"
                "⚠️ <b>После окончания бонусной недели:</b>\n"
                "• Будет автоматически списана полная стоимость: <b>2990 рублей на 30 дней</b>\n"
                "• Автопродление можно отключить в меню «Управление доступом» до окончания бонусной недели\n\n"
            )
        else:
            bonus_warning = ""
        
        logger.info(f"📤 Отправка сообщения об успешной оплате пользователю {tg_user_id}")
        logger.info(f"🔍 Детали: invite_link={invite_link}, starts_str={starts_str}, expires_str={expires_str}, duration_text={duration_text}")
        logger.info(f"🔍 Бонусная неделя активна: {is_bonus_week_active()}, bonus_warning={bonus_warning}")
        try:
            notification_text = (
                "✅ <b>Оплата подтверждена!</b>\n\n"
                f"📅 <b>Доступ активен с:</b> {starts_str}\n"
                f"📅 <b>Доступ активен до:</b> {expires_str}\n\n"
                f"⏱️ <b>Длительность доступа:</b> {duration_text}\n"
                f"{bonus_warning}"
                "⚙️ <b>Управление доступом:</b> В меню появилась кнопка «⚙️ Управление доступом» для управления автопродлением.\n\n"
                "🔗 <b>Ваша индивидуальная ссылка на канал:</b>\n\n"
                f"{invite_link}\n\n"
                "⚠️ <b>ВАЖНО:</b>\n"
                "• Ссылка индивидуальная - её может использовать только вы\n"
                "• Ссылка привязана к вашему аккаунту\n"
                "• При переходе по ссылке вам нужно будет подать заявку на вступление\n"
                "• Заявка будет автоматически одобрена только для вас\n"
                "• Другие пользователи не смогут использовать вашу ссылку\n"
                "• Не передавайте ссылку другим людям - она работает только для вас"
            )
            logger.info(f"📝 Текст уведомления (первые 200 символов): {notification_text[:200]}...")
            
            # Меню уже создано принудительно выше - просто логируем
            menu_buttons_before = [btn.text for row in menu.keyboard for btn in row] if hasattr(menu, 'keyboard') else 'N/A'
            logger.info(f"🔍 ФИНАЛЬНАЯ ПРОВЕРКА перед отправкой сообщения об оплате: menu={menu_buttons_before}, is_bonus_week_active={is_bonus_week_active()}")
            
            # Отправляем сообщение об успешной оплате с ПРАВИЛЬНЫМ меню
            await safe_send_message(
                bot=bot,
                chat_id=tg_user_id,
                text=notification_text,
                parse_mode="HTML",
                reply_markup=menu
            )
            
            # ВАЖНО: Отправляем отдельное сообщение с обновленным меню для гарантированного обновления клавиатуры
            # Это необходимо, так как Telegram может не обновить меню автоматически
            await asyncio.sleep(1.0)  # Увеличиваем задержку для гарантии, что БД обновилась
            
            # Получаем меню еще раз для гарантии актуальности
            from db import _clear_cache
            _clear_cache()
            
            # Проверяем еще раз напрямую в БД
            async with aiosqlite.connect(DB_PATH) as db_final_check:
                cursor_final = await db_final_check.execute(
                    "SELECT expires_at FROM subscriptions WHERE telegram_id = ?",
                    (tg_user_id,)
                )
                row_final = await cursor_final.fetchone()
                if row_final and row_final[0]:
                    from datetime import timezone
                    expires_at_final = datetime.fromisoformat(row_final[0])
                    if expires_at_final.tzinfo is None:
                        expires_at_final = expires_at_final.replace(tzinfo=timezone.utc)
                    now_final = datetime.now(timezone.utc)
                    is_active_final = expires_at_final > now_final
                    logger.info(f"🔍 Финальная проверка БД перед обновлением меню: is_active={is_active_final}")
            
            _clear_cache()
            
            # ВАЖНО: Принудительно создаем правильное меню для бонусной недели
            # КРИТИЧЕСКИ ВАЖНО: Всегда показываем "Управление доступом" после успешной оплаты
            # независимо от результата has_active_subscription, так как подписка только что была активирована
            from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
            BTN_MANAGE_SUB = "⚙️ Управление доступом"
            BTN_ABOUT_1 = "ℹ️ О проекте"
            
            if is_bonus_week_active():
                # В бонусной неделе всегда показываем "Управление доступом" после оплаты
                updated_menu = ReplyKeyboardMarkup(
                    keyboard=[
                        [KeyboardButton(text=BTN_MANAGE_SUB)],
                        [KeyboardButton(text=BTN_ABOUT_1)],
                    ],
                    resize_keyboard=True,
                )
                logger.info(f"✅ Принудительно создано меню с 'Управление доступом' для пользователя {tg_user_id} (бонусная неделя)")
            else:
                # В продакшн режиме тоже всегда показываем "Управление доступом" после оплаты
                updated_menu = ReplyKeyboardMarkup(
                    keyboard=[
                        [KeyboardButton(text=BTN_MANAGE_SUB)],
                        [KeyboardButton(text=BTN_ABOUT_1)],
                    ],
                    resize_keyboard=True,
                )
                logger.info(f"✅ Принудительно создано меню с 'Управление доступом' для пользователя {tg_user_id} (продакшн)")
            
            # Меню уже отправлено вместе с уведомлением об успешной оплате выше
            logger.info(f"✅ Сообщение об успешной оплате отправлено пользователю {tg_user_id}")
        except Exception as send_error:
            logger.error(f"❌ Ошибка отправки сообщения об успешной оплате пользователю {tg_user_id}: {send_error}")
            import traceback
            logger.error(f"❌ Трассировка ошибки: {traceback.format_exc()}")
            traceback.print_exc()
    else:
        # Если ссылка не создана, отправляем сообщение без ссылки
        logger.warning(f"⚠️ Ссылка на канал не была создана для пользователя {tg_user_id}, отправляем сообщение без ссылки")
        
        # Форматируем даты для отображения
        if starts_at_dt and expires_at_dt:
            starts_str = format_datetime_moscow(starts_at_dt)
            expires_str = format_datetime_moscow(expires_at_dt)
        else:
            starts_at_dt = datetime.now(timezone.utc)
            if is_bonus_week_active():
                expires_at_dt = starts_at_dt + timedelta(days=subscription_duration)
            else:
                expires_at_dt = starts_at_dt + timedelta(days=SUBSCRIPTION_DAYS)
            starts_str = format_datetime_moscow(starts_at_dt)
            expires_str = format_datetime_moscow(expires_at_dt)

        # ВАЖНО: Принудительно обновляем меню после оплаты, чтобы показать правильные кнопки
        menu = await get_main_menu_for_user(tg_user_id)
        
        # Форматируем длительность доступа для отображения (используем subscription_duration из активации)
        # КРИТИЧЕСКИ ВАЖНО: Для бонусной недели вычисляем длительность в минутах
        if is_bonus_week_active() and starts_at_dt and expires_at_dt:
            # Вычисляем разницу в минутах для бонусной недели
            time_diff = expires_at_dt - starts_at_dt
            minutes_diff = int(time_diff.total_seconds() / 60)
            if minutes_diff == 1:
                duration_text = "1 минута"
            elif 2 <= minutes_diff <= 4:
                duration_text = f"{minutes_diff} минуты"
            else:
                duration_text = f"{minutes_diff} минут"
        else:
            # Для продакшн режима используем обычное форматирование
            duration_text = format_subscription_duration(subscription_duration)
        
        # Формируем текст в зависимости от режима (бонусная неделя или продакшн)
        if is_bonus_week_active():
            bonus_warning = (
                "\n\n🎉 <b>БОНУСНАЯ НЕДЕЛЯ</b>\n"
                f"⏰ Ваш доступ действует до окончания бонусной недели\n\n"
                "⚠️ <b>После окончания бонусной недели:</b>\n"
                "• Будет автоматически списана полная стоимость: <b>2990 рублей на 30 дней</b>\n"
                "• Автопродление можно отключить в меню «Управление доступом» до окончания бонусной недели\n\n"
            )
        else:
            bonus_warning = ""
        
        await safe_send_message(
            bot=bot,
            chat_id=tg_user_id,
            text="✅ <b>Оплата подтверждена!</b>\n\n"
                f"📅 <b>Доступ активен с:</b> {starts_str}\n"
                f"📅 <b>Доступ активен до:</b> {expires_str}\n\n"
                f"⏱️ <b>Длительность доступа:</b> {duration_text}\n"
                f"🔄 <b>Автопродление:</b> каждые {duration_text}\n"
                f"{bonus_warning}"
                "Для получения доступа к каналу используйте кнопку 📊 Статус доступа.",
            parse_mode="HTML",
            reply_markup=menu
        )

    await mark_processed(payment_id)
    return {"ok": True, "payment_id": payment_id}


# ================== TELEGRAM WEBHOOK (для получения обновлений от Telegram) ==================
@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """
    Обработчик webhook от Telegram для получения обновлений (включая заявки на вступление)
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Обрабатываем заявку на вступление в канал
    if "chat_join_request" in data:
        try:
            from aiogram.types import Update
            update = Update(**data)
            
            if update.chat_join_request:
                chat_join = update.chat_join_request
                user_id = chat_join.from_user.id
                chat_id = chat_join.chat.id

                # Если пользователь оплатил - автоматически одобряем заявку
                if await is_user_allowed(user_id) and chat_id == CHANNEL_ID:
                    try:
                        await bot.approve_chat_join_request(
                            chat_id=chat_id,
                            user_id=user_id
                        )
                        return {"ok": True, "approved": True}
                    except Exception as e:
                        # Логируем ошибку, но не падаем
                        logger.error(f"Error approving join request: {e}")
                        return {"ok": True, "approved": False, "error": str(e)}
                else:
                    # Пользователь не оплатил или это не наш канал
                    return {"ok": True, "approved": False}
        except Exception as e:
            logger.error(f"Error processing chat_join_request: {e}")
            return {"ok": True, "error": str(e)}

    return {"ok": True}


# ================== JOIN REQUEST HANDLER (старый формат, для совместимости) ==================
@app.post("/telegram/join_request")
async def telegram_join_request(request: Request):
    """
    Старый обработчик заявок (для обратной совместимости)
    """
    try:
        data = await request.json()
        
        # Проверяем разные форматы данных
        if "chat_join_request" in data:
            chat_join_data = data["chat_join_request"]
        elif isinstance(data, dict) and "from_user" in data:
            chat_join_data = data
        else:
            return {"ok": True, "ignored": "unknown format"}

        user_id = chat_join_data.get("from_user", {}).get("id") or chat_join_data.get("user", {}).get("id")
        chat_id = chat_join_data.get("chat", {}).get("id")

        if not user_id:
            return {"ok": True, "ignored": "no user_id"}

        user_id = int(user_id)

        # Если пользователь оплатил - автоматически одобряем заявку
        if await is_user_allowed(user_id) and (not chat_id or int(chat_id) == CHANNEL_ID):
            try:
                await bot.approve_chat_join_request(
                    chat_id=chat_id or CHANNEL_ID,
                    user_id=user_id
                )
                return {"ok": True, "approved": True}
            except Exception as e:
                logger.error(f"Error approving join request: {e}")
                return {"ok": True, "approved": False, "error": str(e)}

        return {"ok": True, "approved": False}
    except Exception as e:
        logger.error(f"Error in join_request handler: {e}")
        return {"ok": True, "error": str(e)}

